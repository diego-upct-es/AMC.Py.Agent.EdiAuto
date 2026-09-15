"""Puerto para la cola de mensajes usada entre IncidentProcessor y Worker (Azure Service Bus)."""

from abc import ABC, abstractmethod


class MessageQueueSenderPort(ABC):

    @abstractmethod
    async def send(self, payload: dict, delay_seconds: int | None = None) -> None:
        """
        Encola un mensaje. Si delay_seconds se indica, el mensaje no se hace
        visible para su consumo hasta que transcurra ese tiempo (usado por el
        estado "waiting" para reprogramar la comprobación de una condición
        externa, sin reintentar de inmediato).
        """
        raise NotImplementedError


class MessageQueueReceiverPort(ABC):

    @abstractmethod
    async def receive_one(self, max_wait_seconds: int = 30) -> tuple[dict | None, object | None]:
        """Devuelve (payload, handle). (None, None) si no hay mensajes o la cola no está configurada."""
        raise NotImplementedError

    @abstractmethod
    async def complete(self, handle: object) -> None:
        raise NotImplementedError

    @abstractmethod
    async def dead_letter(self, handle: object, reason: str) -> None:
        raise NotImplementedError
