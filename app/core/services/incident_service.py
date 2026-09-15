"""
Servicio de consulta y gestión de incidencias EDI.

Encapsula los casos de uso para los endpoints de app/api/routes/incidents.py.
Depende de IncidentRepositoryPort (puerto), no de la implementación Postgres
concreta — arquitectura hexagonal.
"""

from app.core.ports.incident_repository_port import IncidentRepositoryPort
from app.models.incident import IncidentResponse


class IncidentNotFoundError(Exception):
    def __init__(self, case_id: str):
        self.case_id = case_id
        super().__init__(f"Caso '{case_id}' no encontrado")


class IncidentService:

    def __init__(self, repository: IncidentRepositoryPort):
        self._repository = repository

    async def list_incidents(
        self, status: str | None, limit: int, offset: int
    ) -> list[IncidentResponse]:
        return await self._repository.list_incidents(status, limit, offset)

    async def get_incident(self, case_id: str) -> IncidentResponse:
        incident = await self._repository.get_incident(case_id)
        if incident is None:
            raise IncidentNotFoundError(case_id)
        return incident

    async def reprocess_incident(self, case_id: str) -> None:
        if not await self._repository.has_processed_email(case_id):
            raise IncidentNotFoundError(case_id)
        await self._repository.mark_incident_for_reprocess(case_id)
