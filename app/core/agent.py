"""
Agente IA del sistema.

Responsabilidad: las tres llamadas a Oracle Generative AI.
El agente NO orquesta el flujo completo — eso lo hace incident_processor
y worker. El agente solo razona sobre texto y produce JSON estructurado.

Llamada 1 + 2 → extract_and_plan()  — usado por IncidentProcessor
Llamada 3     → draft_response()    — usado por Worker (al final del plan)
"""

import json
import structlog

from app.core.ports.ai_port import AIPort
from app.core.ports.incident_repository_port import IncidentRepositoryPort
from app.models.incident import ExtractedInfo, ActionPlan

logger = structlog.get_logger()

# ── Prompts ────────────────────────────────────────────────────────────────────

_EXTRACTION_PROMPT = """\
Analiza este email de incidencia EDI y extrae información estructurada.

ASUNTO: {subject}

CUERPO:
{body}

Extrae la siguiente información en JSON:
{{
    "client_code": "Código del cliente (NORDCART, RETAILCO, GLOBEX, etc). Null si no se menciona",
    "doc_type": "Tipo mensaje EDI (DESADV, INVOIC, ORDERS, etc). Null si no se menciona",
    "doc_number": "Número de documento si se menciona. Null si no",
    "delivery_number": "Número de albarán si se menciona. Null si no",
    "problem_description": "Descripción clara del problema en 1-2 frases",
    "problem_type": "not_received / wrong_data / resend_request / system_error / other",
    "urgency": "LOW / MEDIUM / HIGH / CRITICAL",
    "additional_context": "Información relevante adicional. Null si no hay",
    "contact_info": "Email de contacto si se menciona. Null si no",
    "gln_origen": "GLN (número de 13 dígitos) del punto operacional origen si se menciona. Null si no (típico en errores de 'punto operacional desconocido')",
    "ediwin_domain": "Consola Ediwin/Edicom afectada si se menciona (AMC, PTX, etc). Null si no se menciona"
}}

Responde SOLO con JSON válido. Sin markdown. Sin texto adicional.\
"""

_ACTION_PLAN_PROMPT = """\
Analiza esta incidencia EDI y decide el mejor plan de acción.

INCIDENCIA:
{extracted_info}

CASOS SIMILARES RESUELTOS ANTES:
{historical_cases}

PROCEDIMIENTOS DISPONIBLES:
{procedures}

FLUJO EDI ESPERADO PARA ESTE CLIENTE (vacío si aún no está configurado):
{client_flow_config}

HERRAMIENTAS DISPONIBLES PARA EJECUTAR:
{available_tools}

Decide las acciones a ejecutar en orden. Responde con JSON:
{{
    "actions": [
        {{
            "order": 1,
            "tool": "nombre_de_la_herramienta",
            "arguments": {{"param1": "value1"}},
            "reason": "Por qué esta acción primero",
            "estimated_wait_minutes": null
        }}
    ],
    "expected_outcome": "Qué debería pasar si todo va bien",
    "risk_level": "LOW / MEDIUM / HIGH",
    "requires_human": true,
    "human_action_description": "Qué debe hacer el humano si requires_human=true. Null si false"
}}

REGLAS:
- Primero diagnosticar, luego actuar
- Máximo 5 acciones
- EXCEPCIÓN 1 (evalúa esto antes que cualquier otra regla sobre duda): si una incidencia parece no
  encajar con el procedimiento documentado solo por texto de alarma o advertencia en el correo/adjunto
  (p.ej. "se han rechazado todas las facturas", "fallo crítico"), comprueba primero si sigue siendo, en
  la práctica, el mismo caso simple ya documentado (mismo número de documento único, mismo tipo de
  error). Si lo es, sigue el procedimiento documentado con requires_human=false; ese texto de alarma no
  es, por sí solo, motivo de duda.
- EXCEPCIÓN 2 (evalúa esto antes que cualquier otra regla sobre duda): las herramientas marcadas como
  "NO DISPONIBLE TODAVÍA" no se pueden ejecutar hoy, pero SÍ inclúyelas en el plan si el procedimiento
  las necesita (quedan documentadas para completarlas a mano). Que el plan incluya una herramienta no
  disponible NO es, por sí solo, motivo para poner requires_human=true: si el resto de pasos son claros
  y ejecutables, decide requires_human=false igualmente. Ese paso concreto quedará pendiente de
  completar manualmente al terminar el resto, sin bloquear el resto del plan.
- Fuera de las dos excepciones anteriores, si sigue habiendo duda real sobre el diagnóstico o los pasos
  a seguir, requires_human=true
- Si un paso depende de que un sistema externo termine de procesar algo (p.ej. que Edicom reprocese un
  mensaje corregido) y el procedimiento documentado indica, en las notas de ese paso, cuánto tarda
  habitualmente esa espera (p.ej. "Edicom tarda normalmente 2 horas en reprocesar"), incluye esa
  estimación en minutos en "estimated_wait_minutes" para ese paso. Si el procedimiento no documenta
  ninguna estimación para ese paso, deja "estimated_wait_minutes" en null (se usará un valor estándar).
- Si no hay herramientas aplicables, devuelve actions=[] y requires_human=true
- Responde SOLO con JSON válido\
"""

_RESPONSE_DRAFT_PROMPT = """\
Redacta un email profesional de respuesta al cliente sobre esta incidencia EDI.

INCIDENCIA ORIGINAL:
{incident}

ACCIONES REALIZADAS:
{actions_results}

RESULTADO FINAL: {outcome}

El email debe:
- Ser en español (o el idioma del email original si es diferente)
- Ser profesional y conciso
- Confirmar qué se ha hecho
- Indicar tiempos si aplica
- Indicar siguiente paso si es necesario
- Firmarse como "Equipo de Soporte EDI - AMC Global"

Responde con JSON:
{{
    "subject": "Asunto del email de respuesta",
    "body": "Cuerpo del email (puede incluir saltos de línea con \\n)"
}}\
"""

# Herramientas disponibles que se pasan a la IA en la llamada 2
_AVAILABLE_TOOLS = [
    # ── Edicom (pendientes de API con Edicom — devuelven 501 hasta entonces) ──
    {"name": "edicom.check_message_status",    "description": "[NO DISPONIBLE TODAVÍA — no ejecutable hoy, no lo uses como motivo para requires_human=true] Verifica el estado de un mensaje en Edicom por messageId, docNumber o clientCode"},
    {"name": "edicom.get_message_details",     "description": "[NO DISPONIBLE TODAVÍA — no ejecutable hoy, no lo uses como motivo para requires_human=true] Obtiene detalles completos de un mensaje en Edicom (incluye GLN origen, tipo de documento)"},
    {"name": "edicom.resend_message",          "description": "[NO DISPONIBLE TODAVÍA — no ejecutable hoy, no lo uses como motivo para requires_human=true] Reenvía un mensaje fallido desde Edicom [SIDE-EFFECT]"},
    # ── ERP / SAP ─────────────────────────────────────────────────────────────
    {"name": "erp.get_delivery",               "description": "Obtiene datos de una entrega/albarán en SAP por deliveryNumber"},
    {"name": "erp.search_order",               "description": "Busca un pedido de venta en SAP por orderNumber o customerOrderNumber"},
    {"name": "erp.get_invoice",                "description": "Obtiene datos de una factura en SAP por invoiceNumber (precio, líneas, cliente)"},
    {"name": "erp.get_idoc_details",           "description": "Consulta el IDoc de un documento SAP por idocNumber"},
    {"name": "erp.search_partner_by_gln",      "description": "Busca un interlocutor comercial en SAP por GLN (13 dígitos). Útil para identificar emisores desconocidos en errores de punto operacional"},
    # ── OM / WAS ──────────────────────────────────────────────────────────────
    {"name": "om_was.get_process_status",      "description": "Consulta estado de proceso de transformación EDI en OM o WAS"},
    # ── Filesystem (vía SysFileHub — implementado) ────────────────────────────
    {"name": "filesystem.check_file_location", "description": "Verifica si existe un fichero EDI concreto en una carpeta de red (path completo //server/share/...)"},
    {"name": "filesystem.list_files",          "description": "Lista ficheros en una carpeta de red EDI. Admite filtro por extensión y maxResults"},
    {"name": "filesystem.read_file",           "description": "Lee el contenido texto de un fichero EDI desde la red (path completo)"},
    {"name": "filesystem.write_file",          "description": "Escribe o sobreescribe un fichero en la red [SIDE-EFFECT]. Requiere path completo y content (texto)"},
    {"name": "filesystem.move_file",           "description": "Mueve un fichero entre carpetas de la red [SIDE-EFFECT]. Requiere sourcePath y destinationPath"},
    # ── PCAE (implementado) ───────────────────────────────────────────────────
    {"name": "pcae.create_entry",              "description": "Crea una entrada de seriación en PCAE para resolver el error 'Not serial number available'. Enviar el contenido JSON del log de error como body."},
]


class EDIAgent:

    def __init__(self, oracle_ai: AIPort, repository: IncidentRepositoryPort):
        self._ai = oracle_ai
        self._repository = repository

    # ── Llamadas 1 + 2 (usadas por IncidentProcessor) ─────────────────────────

    async def extract_and_plan(
        self,
        subject: str,
        body: str,
        case_id: str,
    ) -> tuple[ExtractedInfo, ActionPlan]:
        """
        Llamada 1: extrae información estructurada del correo.
        Llamada 2: decide el plan de acción.
        Devuelve (extracted_info, action_plan).
        """
        log = logger.bind(case_id=case_id)

        # ── LLAMADA IA 1: Extracción ──────────────────────────────────────────
        log.info("AI call 1: extracting info from email")
        # Límite generoso, no ajustado al pie del cuerpo del correo: el detalle
        # que decide el plan (número de documento, importe correcto) suele
        # venir en el adjunto, que incident_processor.py añade después del
        # cuerpo — un límite corto puede cortarlo sin que se note.
        extraction_prompt = _EXTRACTION_PROMPT.format(
            subject=subject,
            body=body[:12000],
        )
        extracted_raw = await self._ai.analyze_json(extraction_prompt)
        extracted = ExtractedInfo(**extracted_raw)
        log.info(
            "Info extracted",
            problem_type=extracted.problem_type,
            urgency=extracted.urgency,
            client=extracted.client_code,
        )

        # ── Búsqueda en knowledge base (SQL directo — no IA) ──────────────────
        similar_cases = await self._fetch_similar_cases(extracted)
        procedures = await self._fetch_procedures(extracted)
        client_flow_config = await self._fetch_client_config(extracted)

        # ── LLAMADA IA 2: Plan de acción ──────────────────────────────────────
        log.info("AI call 2: building action plan")
        plan_prompt = _ACTION_PLAN_PROMPT.format(
            extracted_info=json.dumps(extracted.model_dump(), ensure_ascii=False),
            historical_cases=json.dumps(similar_cases, ensure_ascii=False),
            procedures=json.dumps(procedures, ensure_ascii=False),
            client_flow_config=json.dumps(client_flow_config, ensure_ascii=False),
            available_tools=json.dumps(_AVAILABLE_TOOLS, ensure_ascii=False),
        )
        plan_raw = await self._ai.analyze_json(plan_prompt)
        action_plan = ActionPlan(**plan_raw)
        log.info(
            "Action plan ready",
            steps=len(action_plan.actions),
            requires_human=action_plan.requires_human,
            risk=action_plan.risk_level,
        )

        return extracted, action_plan

    # ── Llamada 3 (usada por Worker al final del plan) ─────────────────────────

    async def draft_response(
        self,
        extracted_info: dict,
        steps_results: list[dict],
        outcome: str,
        case_id: str,
    ) -> dict:
        """
        Llamada 3: redacta el email de respuesta al cliente.
        Devuelve {"subject": str, "body": str}.
        """
        log = logger.bind(case_id=case_id, outcome=outcome)
        log.info("AI call 3: drafting response email")

        response_prompt = _RESPONSE_DRAFT_PROMPT.format(
            incident=json.dumps(extracted_info, ensure_ascii=False),
            actions_results=json.dumps(steps_results, ensure_ascii=False, default=str),
            outcome=outcome,
        )
        return await self._ai.analyze_json(response_prompt)

    # ── Helpers privados ───────────────────────────────────────────────────────

    async def _fetch_similar_cases(self, extracted: ExtractedInfo) -> list:
        return await self._repository.fetch_similar_cases(
            extracted.problem_type, extracted.doc_type, client_code=extracted.client_code
        )

    async def _fetch_procedures(self, extracted: ExtractedInfo) -> list:
        return await self._repository.fetch_procedures(
            extracted.problem_type, extracted.doc_type
        )

    async def _fetch_client_config(self, extracted: ExtractedInfo) -> list:
        """
        Flujo EDI esperado para este cliente (edi_flow_configurations) — hoy
        siempre vacío porque la tabla aún no se ha poblado, pero ya queda
        conectado para cuando exista ese catálogo.
        """
        if not extracted.client_code:
            return []
        return await self._repository.get_client_config(
            extracted.client_code, console=extracted.ediwin_domain
        )
