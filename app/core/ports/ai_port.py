"""Puerto para el modelo de IA generativa usado por EDIAgent."""

from abc import ABC, abstractmethod


class AIPort(ABC):

    @abstractmethod
    async def chat(self, prompt: str) -> str:
        """Envía un prompt y devuelve la respuesta como texto plano."""
        raise NotImplementedError

    @abstractmethod
    async def analyze_json(self, prompt: str) -> dict:
        """Envía un prompt y parsea la respuesta como JSON."""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
