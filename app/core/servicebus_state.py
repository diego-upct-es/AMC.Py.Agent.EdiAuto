"""
Estado del listener de Service Bus, compartido entre ServiceBusReceiver y
el health check.

ServiceBusReceiver actualiza este estado en cada intento de recepción
(haya o no mensaje) — el /health lo lee para distinguir "vivo sin
tráfico" de "colgado", algo que el estado interno (running/error) por
sí solo no puede decir si el heartbeat lleva mucho tiempo sin moverse.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class ListenerStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class ServiceBusListenerState:
    status: ListenerStatus = ListenerStatus.STARTING
    last_heartbeat_at: datetime | None = None
    last_message_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    messages_processed: int = 0

    def mark_running(self) -> None:
        self.status = ListenerStatus.RUNNING

    def heartbeat(self) -> None:
        self.last_heartbeat_at = datetime.now(timezone.utc)

    def mark_message_processed(self) -> None:
        now = datetime.now(timezone.utc)
        self.last_message_at = now
        self.last_heartbeat_at = now
        self.messages_processed += 1

    def mark_error(self, error: str) -> None:
        self.status = ListenerStatus.ERROR
        self.last_error = error
        self.last_error_at = datetime.now(timezone.utc)

    def mark_stopped(self) -> None:
        self.status = ListenerStatus.STOPPED

    def seconds_since_heartbeat(self) -> float | None:
        if self.last_heartbeat_at is None:
            return None
        return (datetime.now(timezone.utc) - self.last_heartbeat_at).total_seconds()


# Instancia única del proceso: ServiceBusReceiver escribe, /health lee.
listener_state = ServiceBusListenerState()
