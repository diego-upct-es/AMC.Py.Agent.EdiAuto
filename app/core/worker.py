"""
Worker de Azure Service Bus.

Responsabilidad: ejecutar UN paso del plan de acción por iteración.

Patrón asíncrono:
  - El worker recibe un mensaje con {incident_id, step_index, action_plan, steps_results, ...}
  - Ejecuta el paso indicado por step_index llamando a ActionExecutor → SysEdi
  - Actualiza la BD con el resultado del paso
  - Si hay más pasos: re-encola el siguiente paso en Azure SB
  - Si es el último paso: llama a la llamada IA 3 (draft_response) → envía email → cierra el caso

Ventajas de este patrón:
  - Los workers nunca quedan bloqueados esperando respuestas externas
  - Si hay que esperar a que un sistema externo termine de procesar algo, el
    caso queda en 'waiting' y se reactiva solo, sin reintentar en bucle
  - Múltiples incidencias se procesan de forma interleaved sin bloqueos
  - Reintentos acotados (MAX_STEP_RETRIES) ante un fallo transitorio de un
    paso, antes de escalarlo como definitivo
"""

import asyncio
import structlog

from app.config import get_settings
from app.core.agent import EDIAgent
from app.core.action_executor import ActionExecutor
from app.core.escalation_email import build_escalation_body, build_pending_manual_step_body
from app.core.ports.incident_repository_port import IncidentRepositoryPort
from app.core.ports.mailbox_port import MailboxPort
from app.core.ports.message_queue_port import MessageQueueSenderPort, MessageQueueReceiverPort
from app.models.incident import IncidentQueueMessage, ActionResult

logger = structlog.get_logger()

# Máximo de reintentos de un paso antes de escalarlo. También acota el número
# de comprobaciones reprogramadas del estado "waiting" (misma cota, ver
# sección "Gestión de fallos y reintentos").
MAX_STEP_RETRIES = 3

# Retraso estándar, en minutos, para reprogramar la comprobación de una
# condición externa cuando el procedimiento documentado no indica una
# estimación propia para ese paso (ActionItem.estimated_wait_minutes).
DEFAULT_WAITING_DELAY_MINUTES = 30


class Worker:
    """
    Worker que consume mensajes de Azure Service Bus y ejecuta pasos del plan.
    Se ejecuta como tarea de background mientras el microservicio está activo.
    """

    def __init__(
        self,
        agent: EDIAgent,
        executor: ActionExecutor,
        mailing: MailboxPort,
        sb_sender: MessageQueueSenderPort,
        sb_receiver: MessageQueueReceiverPort,
        repository: IncidentRepositoryPort,
    ):
        self._agent = agent
        self._executor = executor
        self._mailing = mailing
        self._sb_sender = sb_sender
        self._sb_receiver = sb_receiver
        self._repository = repository
        self._running = False

    async def start(self) -> None:
        """Bucle principal del worker. Se ejecuta indefinidamente."""
        self._running = True
        settings = get_settings()
        logger.info(
            "Worker started",
            queue=settings.get_QueueName,
        )

        while self._running:
            try:
                payload, sb_handle = await self._sb_receiver.receive_one(
                    max_wait_seconds=30
                )
                if payload is None:
                    # Sin mensajes en el timeout, o Service Bus no configurado
                    # (receive_one devuelve al instante en ese caso) — ceder el
                    # control al event loop en vez de reintentar en bucle cerrado.
                    await asyncio.sleep(1)
                    continue

                await self._process_step(payload, sb_handle)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Worker loop error", error=str(exc))
                await asyncio.sleep(5)

    def stop(self) -> None:
        self._running = False

    # ── Procesamiento de un paso ───────────────────────────────────────────────

    async def _process_step(self, payload: dict, sb_handle) -> None:
        try:
            msg = IncidentQueueMessage(**payload)
        except Exception as exc:
            logger.error("Invalid queue message format", error=str(exc), payload=payload)
            await self._sb_receiver.dead_letter(sb_handle, reason="InvalidFormat")
            return

        log = logger.bind(
            incident_id=msg.incident_id,
            step_index=msg.step_index,
        )

        action_plan = msg.action_plan
        actions = action_plan.get("actions", [])
        total_steps = len(actions)

        if msg.step_index >= total_steps:
            log.warning("step_index out of range — completing message")
            await self._sb_receiver.complete(sb_handle)
            return

        current_action = actions[msg.step_index]
        log.info("Executing step", tool=current_action.get("tool"))

        # ── Ejecutar el paso ──────────────────────────────────────────────────
        result: ActionResult = await self._executor.execute_step(current_action)

        # Guardar resultado del paso en BD
        updated_results = msg.steps_results + [result.model_dump(mode="json")]
        await self._repository.save_step_result(msg.incident_id, updated_results)

        is_last_step = msg.step_index == total_steps - 1
        step_failed = not result.success
        requires_human = action_plan.get("requires_human", True)

        # Herramienta todavía no implementada en SysEdi (no un fallo real de
        # ejecución) y todos los pasos anteriores se completaron con éxito →
        # el plan se detiene aquí, pero no se trata como un escalado genérico
        # (ver _finalize_incident, caso "pending_manual_step").
        is_pending_tool = step_failed and result.error_type == "not_implemented"
        # Condición externa todavía no cumplida (p. ej. Edicom todavía procesando
        # un mensaje corregido): distinta de un fallo transitorio (reintentar de
        # inmediato no tiene sentido) y de una herramienta no implementada (la
        # herramienta sí se ejecutó, solo que su resultado no está disponible).
        is_external_pending = step_failed and result.error_type == "external_pending"
        all_prior_succeeded = all(r.get("success") for r in msg.steps_results)

        # Reencola el mismo paso con un retraso explícito (Azure SB scheduled
        # enqueue) en vez de reintentarlo de inmediato, y marca el caso como
        # "waiting" mientras dura la espera. El retraso sale del propio
        # procedimiento (ActionItem.estimated_wait_minutes, rellenado por la IA
        # a partir de las notas del paso) cuando lo documenta; si no, se usa el
        # retraso estándar. Acotado al mismo MAX_STEP_RETRIES que el resto de
        # reintentos: agotadas las comprobaciones, cae al bloque de escalado.
        if is_external_pending and msg.pending_checks < MAX_STEP_RETRIES - 1:
            delay_minutes = (
                current_action.get("estimated_wait_minutes")
                or result.retry_after_minutes
                or DEFAULT_WAITING_DELAY_MINUTES
            )
            log.info(
                "Step depends on an external condition — rescheduling",
                pending_checks=msg.pending_checks + 1,
                max_checks=MAX_STEP_RETRIES,
                delay_minutes=delay_minutes,
            )
            await self._repository.mark_case_waiting(msg.incident_id)
            waiting_msg = IncidentQueueMessage(
                incident_id=msg.incident_id,
                email_from=msg.email_from,
                email_subject=msg.email_subject,
                extracted_info=msg.extracted_info,
                action_plan=msg.action_plan,
                step_index=msg.step_index,
                # Igual que en un reintento ordinario: se parte de los resultados
                # previos a este intento, no de los acumulados, para que el caso
                # retome el flujo exactamente donde se quedó al reactivarse.
                steps_results=msg.steps_results,
                retry_count=msg.retry_count,
                pending_checks=msg.pending_checks + 1,
            )
            await self._sb_sender.send(waiting_msg.model_dump(), delay_seconds=delay_minutes * 60)
            await self._sb_receiver.complete(sb_handle)
            return

        # Fallo real (no "not_implemented" ni "external_pending") y todavía
        # quedan reintentos: se reintenta el MISMO paso, sin avanzar step_index,
        # antes de darlo por definitivo y escalar. Un fallo transitorio de un
        # sistema externo (timeout, error 5xx puntual) puede no repetirse en el
        # siguiente intento.
        if (
            step_failed
            and not is_pending_tool
            and not is_external_pending
            and msg.retry_count < MAX_STEP_RETRIES - 1
        ):
            log.warning(
                "Step failed — retrying",
                retry_count=msg.retry_count + 1,
                max_retries=MAX_STEP_RETRIES,
                error=result.error,
            )
            await asyncio.sleep(2)
            retry_msg = IncidentQueueMessage(
                incident_id=msg.incident_id,
                email_from=msg.email_from,
                email_subject=msg.email_subject,
                extracted_info=msg.extracted_info,
                action_plan=msg.action_plan,
                step_index=msg.step_index,
                # Se descarta el resultado fallido de este intento (ya quedó
                # registrado en BD para trazabilidad): el reintento debe partir
                # de los resultados previos a este intento, no acumularlos,
                # para que "all_prior_succeeded" siga siendo correcto si éxito.
                steps_results=msg.steps_results,
                retry_count=msg.retry_count + 1,
            )
            await self._sb_sender.send(retry_msg.model_dump())
            await self._sb_receiver.complete(sb_handle)
            return

        # ── Decidir próxima acción ────────────────────────────────────────────
        if not is_last_step and not step_failed:
            # Más pasos por ejecutar y el paso actual tuvo éxito → encolar siguiente
            log.info("Step done — enqueuing next step", next_step=msg.step_index + 1)
            next_msg = IncidentQueueMessage(
                incident_id=msg.incident_id,
                email_from=msg.email_from,
                email_subject=msg.email_subject,
                extracted_info=msg.extracted_info,
                action_plan=msg.action_plan,
                step_index=msg.step_index + 1,
                steps_results=updated_results,
            )
            await self._sb_sender.send(next_msg.model_dump())
            await self._sb_receiver.complete(sb_handle)
            return

        # Último paso o fallo → cerrar el flujo
        await self._sb_receiver.complete(sb_handle)

        if not step_failed and not requires_human:
            outcome = "resolved"
        elif is_pending_tool and all_prior_succeeded:
            outcome = "pending_manual_step"
        else:
            outcome = "escalated"
        log.info("Plan complete — finalizing", outcome=outcome)

        await self._finalize_incident(
            msg=msg,
            steps_results=updated_results,
            outcome=outcome,
        )

    # ── Finalización del flujo ─────────────────────────────────────────────────

    async def _finalize_incident(
        self,
        msg: IncidentQueueMessage,
        steps_results: list,
        outcome: str,
    ) -> None:
        settings = get_settings()
        log = logger.bind(incident_id=msg.incident_id, outcome=outcome)
        resolved_automatically = outcome == "resolved"
        action_plan = msg.action_plan
        extracted = msg.extracted_info

        # ── Caso especial: plan ejecutado salvo un paso con herramienta aún no
        # implementada. No se llama a la IA (llamada 3) ni se notifica nada al
        # cliente — solo aviso interno detallando qué se hizo y qué falta. ────
        if outcome == "pending_manual_step":
            log.info("Plan executed up to a not-yet-implemented tool — notifying internally only")
            if settings.internal_alert_email:
                try:
                    await self._mailing.send_email(
                        to=[settings.internal_alert_email],
                        subject=f"[EDI PENDIENTE MANUAL] {extracted.get('problem_description', msg.email_subject)[:80]}",
                        body=build_pending_manual_step_body(
                            case_id=msg.incident_id,
                            extracted=extracted,
                            action_plan=action_plan,
                            steps_results=steps_results,
                            from_email=msg.email_from,
                            subject=msg.email_subject,
                        ),
                    )
                except Exception as exc:
                    log.error("Could not send pending-manual-step email", error=str(exc))

            completed = sum(1 for r in steps_results if r.get("success"))
            await self._repository.finalize_case(
                case_id=msg.incident_id,
                resolved_automatically=False,
                resolution_summary=(
                    f"Resuelto parcialmente: {completed}/{len(steps_results)} pasos completados. "
                    f"Pendiente de completar a mano por herramienta no implementada."
                ),
                requires_human=True,
                status="pending_manual_step",
            )
            log.info("Incident finalized", case_id=msg.incident_id)
            return

        # ── LLAMADA IA 3: Redactar respuesta ──────────────────────────────────
        try:
            email_response = await self._agent.draft_response(
                extracted_info=msg.extracted_info,
                steps_results=steps_results,
                outcome=outcome,
                case_id=msg.incident_id,
            )
        except Exception as exc:
            log.error("AI draft_response failed", error=str(exc))
            email_response = {
                "subject": f"Re: {msg.email_subject}",
                "body": "Se ha procesado su incidencia. El equipo de soporte EDI se pondrá en contacto con usted.",
            }

        # ── Enviar email y actualizar BD ───────────────────────────────────────
        if resolved_automatically:
            log.info("Sending resolution email to client")
            try:
                await self._mailing.send_email(
                    to=[msg.email_from],
                    subject=email_response.get("subject", f"Re: {msg.email_subject}"),
                    body=email_response.get("body", ""),
                )
            except Exception as exc:
                log.error("Could not send resolution email", error=str(exc))
        else:
            log.info("Escalating to internal team")
            if settings.internal_alert_email:
                try:
                    await self._mailing.send_email(
                        to=[settings.internal_alert_email],
                        subject=f"[EDI INCIDENCIA] {extracted.get('problem_description', msg.email_subject)[:80]}",
                        body=build_escalation_body(
                            case_id=msg.incident_id,
                            extracted=extracted,
                            action_plan=action_plan,
                            from_email=msg.email_from,
                            subject=msg.email_subject,
                        ),
                    )
                except Exception as exc:
                    log.error("Could not send escalation email", error=str(exc))

        await self._repository.finalize_case(
            case_id=msg.incident_id,
            resolved_automatically=resolved_automatically,
            resolution_summary=email_response.get("body", "")[:500],
            requires_human=not resolved_automatically,
            status=outcome,
        )
        log.info("Incident finalized", case_id=msg.incident_id)
