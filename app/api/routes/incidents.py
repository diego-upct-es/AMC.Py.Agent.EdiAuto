from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.dependencies import get_incident_repository
from app.core.ports.incident_repository_port import IncidentRepositoryPort
from app.core.services.incident_service import IncidentNotFoundError, IncidentService
from app.models.incident import IncidentResponse

router = APIRouter()


def _get_incident_service(
    repository: IncidentRepositoryPort = Depends(get_incident_repository),
) -> IncidentService:
    return IncidentService(repository)


@router.get(
    "",
    response_model=list[IncidentResponse],
    summary="Listar incidencias EDI registradas",
)
async def list_incidents(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    service: IncidentService = Depends(_get_incident_service),
):
    return await service.list_incidents(status, limit, offset)


@router.get(
    "/{case_id}",
    response_model=IncidentResponse,
    summary="Detalle de una incidencia",
)
async def get_incident(case_id: str, service: IncidentService = Depends(_get_incident_service)):
    try:
        return await service.get_incident(case_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class ReprocessResponse(BaseModel):
    case_id: str
    message: str


@router.post(
    "/{case_id}/reprocess",
    response_model=ReprocessResponse,
    summary="Reprocesar una incidencia manualmente",
)
async def reprocess_incident(
    case_id: str, service: IncidentService = Depends(_get_incident_service)
):
    """
    Marca una incidencia para reprocesamiento.
    El EmailMonitor la detectará en la siguiente iteración y la procesará de nuevo.
    Útil cuando el procesamiento automático ha fallado.
    """
    try:
        await service.reprocess_incident(case_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ReprocessResponse(case_id=case_id, message="Incidencia marcada para reprocesamiento")
