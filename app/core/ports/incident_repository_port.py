"""Puerto de persistencia para incidencias EDI y correos procesados."""

from abc import ABC, abstractmethod
from typing import Any

from app.models.incident import IncidentResponse


class IncidentRepositoryPort(ABC):

    # ── Consulta de incidencias (usado por IncidentService) ────────────────────

    @abstractmethod
    async def list_incidents(
        self, status: str | None, limit: int, offset: int
    ) -> list[IncidentResponse]:
        raise NotImplementedError

    @abstractmethod
    async def get_incident(self, case_id: str) -> IncidentResponse | None:
        raise NotImplementedError

    @abstractmethod
    async def has_processed_email(self, case_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def mark_incident_for_reprocess(self, case_id: str) -> None:
        raise NotImplementedError

    # ── Escritura del flujo (usado por IncidentProcessor / Worker) ─────────────

    @abstractmethod
    async def is_email_already_processed(self, message_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def register_new_case(
        self,
        message_id: str,
        mailbox: str,
        case_id: str,
        subject: str,
        from_email: str,
        received_at: Any,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def save_extraction_and_plan(
        self,
        case_id: str,
        client_code: str | None,
        doc_type: str | None,
        doc_number: str | None,
        problem_type: str | None,
        urgency: str | None,
        console: str | None,
        severity: str | None,
        extracted_info: dict,
        action_plan: dict,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def escalate_case(
        self, case_id: str, resolution_summary: str, mark_email_error: bool = False
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def save_step_result(self, case_id: str, steps_results: list) -> None:
        raise NotImplementedError

    @abstractmethod
    async def mark_case_waiting(self, case_id: str) -> None:
        """Marca el caso como 'waiting': la resolución depende de una condición
        externa todavía no cumplida (sección 'Gestión de fallos y reintentos')."""
        raise NotImplementedError

    @abstractmethod
    async def finalize_case(
        self,
        case_id: str,
        resolved_automatically: bool,
        resolution_summary: str,
        requires_human: bool,
        status: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def fetch_similar_cases(
        self,
        problem_type: str,
        doc_type: str | None,
        client_code: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    async def fetch_procedures(self, problem_type: str, doc_type: str | None) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    async def get_client_config(
        self, client_code: str, console: str | None = None
    ) -> list[dict]:
        """Configuración EDI de un cliente (SLAs, flujos, tipos de mensajes)."""
        raise NotImplementedError

    @abstractmethod
    async def increment_procedure_usage(self, procedure_id: int, success: bool) -> None:
        """Actualiza las estadísticas de uso (times_used, success_rate) de un procedimiento."""
        raise NotImplementedError

    @abstractmethod
    async def check_database_alive(self) -> None:
        """Lanza excepción si la BD no responde. Usado por HealthService."""
        raise NotImplementedError
