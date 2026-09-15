"""
Formato compartido del cuerpo de los emails de escalado a soporte interno.

Antes, IncidentProcessor y Worker construían cada uno su propio texto de
escalado, con campos distintos y ninguno mostraba toda la información ya
disponible (doc_number, GLN, consola, contexto adicional, ni los pasos que
la IA había propuesto). Se centraliza aquí para que cualquier camino de
escalado muestre lo mismo, completo.
"""

from typing import Any


def build_escalation_body(
    case_id: str,
    extracted: dict[str, Any],
    action_plan: dict[str, Any] | None,
    from_email: str,
    subject: str,
    intro: str = "Incidencia EDI requiere intervención humana.",
) -> str:
    action_plan = action_plan or {}
    actions = action_plan.get("actions") or []
    steps_text = "\n".join(
        f"  Paso {a.get('order', i + 1)}: [{a.get('tool')}] {a.get('reason', '')}"
        for i, a in enumerate(actions)
    ) or "  (ninguna acción propuesta)"

    return (
        f"{intro}\n\n"
        f"Caso: {case_id}\n"
        f"Cliente: {extracted.get('client_code') or 'Desconocido'}\n"
        f"Tipo documento: {extracted.get('doc_type') or 'Desconocido'}\n"
        f"Número de documento: {extracted.get('doc_number') or 'Desconocido'}\n"
        f"Número de albarán: {extracted.get('delivery_number') or 'Desconocido'}\n"
        f"Urgencia: {extracted.get('urgency') or 'Desconocido'}\n"
        f"GLN origen: {extracted.get('gln_origen') or 'Desconocido'}\n"
        f"Consola Ediwin/Edicom: {extracted.get('ediwin_domain') or 'Desconocido'}\n"
        f"Contacto indicado en el correo: {extracted.get('contact_info') or 'Desconocido'}\n\n"
        f"Problema: {extracted.get('problem_description') or 'Sin descripción'}\n\n"
        f"Contexto adicional: {extracted.get('additional_context') or '(ninguno)'}\n\n"
        f"Riesgo estimado del plan: {action_plan.get('risk_level') or 'Desconocido'}\n"
        f"Pasos propuestos por la IA:\n{steps_text}\n\n"
        f"Acción requerida: {action_plan.get('human_action_description') or 'Revisar manualmente'}\n\n"
        f"Remitente original: {from_email}\n"
        f"Asunto original: {subject}"
    )


def build_pending_manual_step_body(
    case_id: str,
    extracted: dict[str, Any],
    action_plan: dict[str, Any] | None,
    steps_results: list[dict[str, Any]],
    from_email: str,
    subject: str,
) -> str:
    """
    Cuerpo del email interno cuando el plan se ha ejecutado hasta el final salvo
    por un paso cuya herramienta todavía no está implementada (error_type ==
    "not_implemented"). A diferencia de build_escalation_body, aquí sí importa
    separar qué se ha hecho ya de verdad (steps_results) de lo que queda
    pendiente, porque las acciones con efecto secundario (escribir/mover
    ficheros) ya se han ejecutado.
    """
    action_plan = action_plan or {}
    actions = action_plan.get("actions") or []

    completed_lines: list[str] = []
    pending_lines: list[str] = []
    attempted_orders: set[int] = set()

    for result in steps_results:
        action = result.get("action") or {}
        order = action.get("order")
        attempted_orders.add(order)
        tool = action.get("tool")
        reason = action.get("reason", "")
        if result.get("success"):
            completed_lines.append(f"  Paso {order}: [{tool}] {reason}")
        else:
            motivo = result.get("error") or "Herramienta no disponible todavía."
            pending_lines.append(f"  Paso {order}: [{tool}] {reason}\n    Motivo: {motivo}")

    # Pasos del plan que ni siquiera se llegaron a intentar, por haberse
    # detenido el worker en el primer paso no disponible.
    for a in actions:
        if a.get("order") not in attempted_orders:
            pending_lines.append(f"  Paso {a.get('order')}: [{a.get('tool')}] {a.get('reason', '')}")

    completed_text = "\n".join(completed_lines) or "  (ninguno)"
    pending_text = "\n".join(pending_lines) or "  (ninguno)"

    return (
        "Incidencia EDI resuelta parcialmente de forma automática — queda un paso pendiente de completar a mano.\n\n"
        f"Caso: {case_id}\n"
        f"Cliente: {extracted.get('client_code') or 'Desconocido'}\n"
        f"Tipo documento: {extracted.get('doc_type') or 'Desconocido'}\n"
        f"Número de documento: {extracted.get('doc_number') or 'Desconocido'}\n"
        f"Consola Ediwin/Edicom: {extracted.get('ediwin_domain') or 'Desconocido'}\n\n"
        f"Problema: {extracted.get('problem_description') or 'Sin descripción'}\n\n"
        f"HECHO AUTOMÁTICAMENTE POR EL AGENTE:\n{completed_text}\n\n"
        f"PENDIENTE DE COMPLETAR A MANO:\n{pending_text}\n\n"
        "IMPORTANTE: no se ha notificado nada todavía al cliente ni se ha marcado como resuelto. "
        "Solo debe comunicarse al cliente una vez completado el paso pendiente.\n\n"
        f"Remitente original: {from_email}\n"
        f"Asunto original: {subject}"
    )
