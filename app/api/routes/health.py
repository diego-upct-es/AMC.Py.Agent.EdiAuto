from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.dependencies import get_incident_repository
from app.core.ports.incident_repository_port import IncidentRepositoryPort
from app.core.services.health_service import HealthService

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    database: str
    servicebus_listener: str
    version: str = "1.0.0"
    service: str = "AMC.Py.Agent.EdiAuto"


def _get_health_service(
    repository: IncidentRepositoryPort = Depends(get_incident_repository),
) -> HealthService:
    return HealthService(repository)


@router.get("/health", tags=["Health"], summary="Health check del microservicio")
async def health_check(service: HealthService = Depends(_get_health_service)) -> HealthResponse:
    """
    Verifica que el microservicio y sus dependencias críticas están operativos.
    Equivale al endpoint /health estándar de los microservicios .NET AMC.
    """
    db_status = await service.check_database()
    sb_status = service.check_servicebus_listener()
    overall = "ok" if db_status == "ok" and sb_status == "ok" else "degraded"
    return HealthResponse(status=overall, database=db_status, servicebus_listener=sb_status)
