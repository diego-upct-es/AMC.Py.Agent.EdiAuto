"""
Servicio de health check del microservicio.

Depende de IncidentRepositoryPort (puerto), no de la implementación
Postgres concreta — arquitectura hexagonal.
"""

from app.core.ports.incident_repository_port import IncidentRepositoryPort
from app.core.servicebus_state import ListenerStatus, listener_state

# ~3x el max_wait_seconds con el que Worker llama a receive_one() (30s):
# si no hay heartbeat en ese margen, el listener se considera colgado
# aunque su estado interno siga diciendo "running".
HEARTBEAT_STALE_THRESHOLD_SECONDS = 90


class HealthService:

    def __init__(self, repository: IncidentRepositoryPort):
        self._repository = repository

    async def check_database(self) -> str:
        try:
            await self._repository.check_database_alive()
            return "ok"
        except Exception as exc:
            return f"error: {exc}"

    def check_servicebus_listener(self) -> str:
        if listener_state.status == ListenerStatus.STOPPED:
            return "stopped"
        if listener_state.status == ListenerStatus.ERROR:
            return f"error: {listener_state.last_error}"

        seconds_since = listener_state.seconds_since_heartbeat()
        if seconds_since is None:
            return "starting"
        if seconds_since > HEARTBEAT_STALE_THRESHOLD_SECONDS:
            return f"degraded: sin heartbeat desde hace {int(seconds_since)}s"
        return "ok"
