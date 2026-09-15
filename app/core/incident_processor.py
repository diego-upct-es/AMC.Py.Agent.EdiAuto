"""
Procesador inicial de incidencias EDI.

Responsabilidad: orquesta el inicio del flujo para un correo nuevo:
  1. Obtener contenido completo del correo (.eml)
  2. Moverlo a carpeta "Procesados" (sale del buzón principal)
  3. Registrar en BD (status = processing)
  4. Llamar al agente IA (llamadas 1+2: extract + plan)
  5a. Si no hay acciones o requires_human desde el inicio → escalar directamente
  5b. Si hay acciones → encolar en Azure Service Bus (worker las ejecutará)

El worker se encarga del resto: ejecutar pasos, redactar respuesta (llamada 3),
enviar email y actualizar el estado final.
"""

import uuid
import structlog
from datetime import datetime, timezone

from app.config import get_settings
from app.core.agent import EDIAgent
from app.core.escalation_email import build_escalation_body
from app.core.ports.incident_repository_port import IncidentRepositoryPort
from app.core.ports.mailbox_port import MailboxPort
from app.core.ports.message_queue_port import MessageQueueSenderPort
from app.models.incident import IncidentQueueMessage

logger = structlog.get_logger()


def _generate_case_id() -> str:
    now = datetime.now(timezone.utc)
    suffix = str(uuid.uuid4()).replace("-", "")[:8].upper()
    return f"EDI-{now.strftime('%Y%m%d')}-{suffix}"


class IncidentProcessor:

    def __init__(
        self,
        mailing: MailboxPort,
        agent: EDIAgent,
        sb_sender: MessageQueueSenderPort,
        repository: IncidentRepositoryPort,
    ):
        self._mailing = mailing
        self._agent = agent
        self._sb_sender = sb_sender
        self._repository = repository

    async def process(self, message_metadata: dict) -> None:
        """
        Punto de entrada. Llamado por EmailMonitor por cada correo nuevo.
        """
        settings = get_settings()
        message_id = message_metadata.get("id") or message_metadata.get("messageId", "")
        log = logger.bind(message_id=message_id)

        # ── Verificar idempotencia ────────────────────────────────────────────
        if await self._repository.is_email_already_processed(message_id):
            log.info("Email already processed — skipping")
            return

        # ── PASO 1: Obtener contenido completo del correo ─────────────────────
        log.info("Fetching raw email (.eml)")
        raw_eml = await self._mailing.get_message_raw(message_id)
        parsed = self._mailing.parse_eml(raw_eml, message_id)

        # ── PASO 2: Mover a "Procesados" inmediatamente ───────────────────────
        # Se mueve antes de procesar para que no vuelva a ser detectado
        try:
            await self._mailing.move_message(message_id, "Procesados")
        except Exception as exc:
            log.warning("Could not move email to 'Procesados'", error=str(exc))

        # ── PASO 3: Marcar como leído ─────────────────────────────────────────
        try:
            await self._mailing.mark_as_read(message_id)
        except Exception as exc:
            log.warning("Could not mark email as read", error=str(exc))

        # ── PASO 4: Registrar en BD ───────────────────────────────────────────
        case_id = _generate_case_id()
        log = log.bind(case_id=case_id)

        await self._repository.register_new_case(
            message_id=message_id,
            mailbox=settings.mailing_edi_mailbox,
            case_id=case_id,
            subject=parsed.subject,
            from_email=parsed.from_email,
            received_at=parsed.received_datetime,
        )
        log.info("Case registered in DB")

        # ── PASO 5: Llamadas IA 1+2 — extracción + plan ───────────────────────
        # El texto de los adjuntos PDF (ej. informes de cumplimiento) se añade
        # antes que el cuerpo del correo, no después: el adjunto suele llevar
        # el detalle denso (número de documento, importe correcto) que decide
        # el plan, mientras que el cuerpo del correo suele ser sobre todo firma
        # y cadena de reenvíos — si el límite de la llamada 1 llega a cortar
        # algo, mejor que sea eso y no el adjunto.
        body_with_attachments = "\n\n".join(
            part for part in [*parsed.attachments_text, parsed.body_text or parsed.body_html] if part
        )
        try:
            extracted, action_plan = await self._agent.extract_and_plan(
                subject=parsed.subject,
                body=body_with_attachments,
                case_id=case_id,
            )
        except Exception as exc:
            log.error("AI extract_and_plan failed", error=str(exc))
            await self._escalate_error(case_id, parsed, str(exc))
            return

        # Guardar en BD lo extraído y el plan.
        # console viene de ediwin_domain (misma cosa, dos nombres distintos:
        # la IA la llama "consola Ediwin/Edicom", la tabla la llama "console").
        # severity viene de action_plan.risk_level: mide cuánto riesgo tendría
        # aplicar la corrección propuesta — un dato distinto de urgency, que
        # mide cuánto apremia la incidencia según el propio correo.
        await self._repository.save_extraction_and_plan(
            case_id=case_id,
            client_code=extracted.client_code,
            doc_type=extracted.doc_type,
            doc_number=extracted.doc_number,
            problem_type=extracted.problem_type,
            urgency=extracted.urgency,
            console=extracted.ediwin_domain,
            severity=action_plan.risk_level,
            extracted_info=extracted.model_dump(),
            action_plan=action_plan.model_dump(),
        )

        # ── PASO 6: Encolar en Azure Service Bus o escalar directamente ────────
        has_actions = len(action_plan.actions) > 0

        if not has_actions or action_plan.requires_human:
            # Sin acciones que ejecutar → escalar directamente sin pasar por worker
            log.info("No actions or requires_human — escalating directly")
            await self._escalate_no_actions(case_id, parsed, extracted, action_plan)
            return

        # Si Azure Service Bus no está configurado → fallback: escalar con el plan
        if not settings.get_QueueConnString:
            log.warning(
                "AZURE_SERVICEBUS_CONNECTION_STRING not set — "
                "falling back to direct escalation with plan details"
            )
            await self._escalate_no_servicebus(case_id, parsed, extracted, action_plan)
            return

        # Encolar el primer paso en Azure Service Bus
        queue_msg = IncidentQueueMessage(
            incident_id=case_id,
            email_from=parsed.from_email,
            email_subject=parsed.subject,
            extracted_info=extracted.model_dump(),
            action_plan=action_plan.model_dump(),
            step_index=0,
            steps_results=[],
        )
        await self._sb_sender.send(queue_msg.model_dump())
        log.info(
            "Plan enqueued to Azure Service Bus",
            steps=len(action_plan.actions),
            queue=settings.get_QueueName,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _escalate_error(self, case_id: str, parsed, error: str) -> None:
        """Escalado de emergencia cuando la IA falla."""
        settings = get_settings()
        await self._repository.escalate_case(
            case_id,
            f"Error en el agente IA: {error[:500]}",
            mark_email_error=True,
        )
        if settings.internal_alert_email:
            try:
                await self._mailing.send_email(
                    to=[settings.internal_alert_email],
                    subject=f"[EDI ERROR] {parsed.subject}",
                    body=(
                        f"Error procesando incidencia {case_id}.\n\n"
                        f"De: {parsed.from_email}\n"
                        f"Asunto: {parsed.subject}\n\n"
                        f"Error técnico: {error}\n\n"
                        f"Se requiere intervención manual."
                    ),
                )
            except Exception as exc:
                logger.error("Could not send error escalation", error=str(exc))

    async def _escalate_no_actions(self, case_id: str, parsed, extracted, action_plan) -> None:
        """Escala cuando la IA decide que no hay acciones automáticas posibles."""
        settings = get_settings()
        await self._repository.escalate_case(
            case_id,
            action_plan.human_action_description or "Requiere intervención manual.",
        )
        if settings.internal_alert_email:
            try:
                await self._mailing.send_email(
                    to=[settings.internal_alert_email],
                    subject=f"[EDI INCIDENCIA] {extracted.problem_description}",
                    body=build_escalation_body(
                        case_id=case_id,
                        extracted=extracted.model_dump(),
                        action_plan=action_plan.model_dump(),
                        from_email=parsed.from_email,
                        subject=parsed.subject,
                    ),
                )
            except Exception as exc:
                logger.error("Could not send escalation email", error=str(exc))

    async def _escalate_no_servicebus(
        self, case_id: str, parsed, extracted, action_plan
    ) -> None:
        """
        Fallback para desarrollo local sin Azure Service Bus configurado.
        En lugar de encolar en Azure SB, escala directamente al equipo interno
        incluyendo el plan de acción completo que el agente habría ejecutado.
        Permite validar el flujo completo (IA extrae + IA planifica + email de escalado)
        sin necesitar infraestructura de Azure SB.
        """
        settings = get_settings()
        await self._repository.escalate_case(
            case_id,
            "[Fallback local] Plan generado pero no ejecutado: Azure Service Bus no configurado.",
        )

        if not settings.internal_alert_email:
            logger.warning("INTERNAL_ALERT_EMAIL not set — escalation email skipped")
            return

        try:
            await self._mailing.send_email(
                to=[settings.internal_alert_email],
                subject=f"[EDI LOCAL] {extracted.problem_description[:80]}",
                body=build_escalation_body(
                    case_id=case_id,
                    extracted=extracted.model_dump(),
                    action_plan=action_plan.model_dump(),
                    from_email=parsed.from_email,
                    subject=parsed.subject,
                    intro=(
                        "⚠️ MODO DESARROLLO — Azure Service Bus no configurado. "
                        "El plan ha sido generado pero no ejecutado. Para activar la "
                        "ejecución real, configura AZURE_SERVICEBUS_CONNECTION_STRING en .env."
                    ),
                ),
            )
        except Exception as exc:
            logger.error("Could not send fallback escalation email", error=str(exc))
