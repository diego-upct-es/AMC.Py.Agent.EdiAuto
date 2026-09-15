from pydantic import BaseModel
from datetime import datetime
from typing import Any


class ExtractedInfo(BaseModel):
    client_code: str | None = None
    doc_type: str | None = None
    doc_number: str | None = None
    delivery_number: str | None = None
    problem_description: str
    problem_type: str = "other"
    urgency: str = "MEDIUM"
    additional_context: str | None = None
    contact_info: str | None = None
    gln_origen: str | None = None
    ediwin_domain: str | None = None


class ActionItem(BaseModel):
    order: int
    tool: str
    arguments: dict
    reason: str
    # Estimación, en minutos, de cuánto tarda habitualmente en cumplirse la
    # condición externa de la que depende este paso (p. ej. "Edicom tarda
    # normalmente 2 horas en reprocesar"), cuando el procedimiento documentado
    # incluye esa estimación en las notas del paso. None si no hay estimación
    # documentada — el Worker usa entonces un retraso estándar.
    estimated_wait_minutes: int | None = None


class ActionPlan(BaseModel):
    actions: list[ActionItem]
    expected_outcome: str
    risk_level: str = "MEDIUM"
    requires_human: bool = True
    human_action_description: str | None = None


class ActionResult(BaseModel):
    action: dict
    success: bool
    result: Any | None = None
    error: str | None = None
    # "not_implemented" cuando el fallo es porque la herramienta aún no está
    # implementada en SysEdi (vs un fallo real de ejecución) — permite al
    # Worker decidir si cerrar el caso como "pending_manual_step" en vez de
    # "escalated" cuando es el único motivo por el que el plan no se completó.
    # "external_pending" cuando la herramienta se ejecutó pero su resultado
    # depende de una condición externa todavía no cumplida — ver estado
    # "waiting" en el Worker.
    error_type: str | None = None
    # Estimación de SysEdi, en minutos, de cuándo estará disponible el
    # resultado — solo relevante cuando error_type == "external_pending".
    retry_after_minutes: int | None = None


class IncidentQueueMessage(BaseModel):
    """Mensaje que viaja por Azure Service Bus entre el processor y los workers."""
    incident_id: str
    email_from: str
    email_subject: str
    extracted_info: dict
    action_plan: dict          # ActionPlan serializado
    step_index: int = 0        # Índice del paso a ejecutar ahora
    steps_results: list = []   # Resultados acumulados de pasos anteriores
    retry_count: int = 0       # Reintentos ya consumidos para el step_index actual
    # Comprobaciones reprogramadas ya consumidas por una condición externa
    # pendiente (estado "waiting"), acotadas también a MAX_STEP_RETRIES.
    # Cuenta aparte de retry_count porque es una espera planificada, no un
    # reintento ante un fallo.
    pending_checks: int = 0


class IncidentResponse(BaseModel):
    """Modelo de respuesta para los endpoints REST."""
    case_id: str
    status: str
    client_code: str | None = None
    doc_type: str | None = None
    problem_description: str | None = None
    urgency: str | None = None
    resolved_automatically: bool | None = None
    resolution_summary: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    requires_human: bool | None = None
