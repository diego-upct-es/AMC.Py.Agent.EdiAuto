"""Puerto para el ejecutor de herramientas del agente (AMC.Core.SysEdi)."""

from abc import ABC, abstractmethod


class ExternalPendingError(Exception):
    """
    La herramienta existe y se ha podido ejecutar, pero su resultado depende
    de una condición externa todavía no cumplida (p. ej. que Edicom termine
    de reprocesar un mensaje corregido). Distinta de NotImplementedError
    (la herramienta no existe todavía) y de un fallo real de ejecución.

    retry_after_minutes: estimación, si el adaptador la conoce, de cuánto
    tardará en cumplirse la condición. None si no hay estimación disponible.
    """

    def __init__(self, message: str, retry_after_minutes: int | None = None):
        super().__init__(message)
        self.retry_after_minutes = retry_after_minutes


class ToolExecutorPort(ABC):

    @abstractmethod
    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """
        Ejecuta una herramienta por nombre.

        Raises:
            NotImplementedError: la herramienta aún no está implementada en el backend.
            ExternalPendingError: la herramienta se ejecutó pero su resultado depende
                de una condición externa todavía no cumplida.
            ValueError: el tool_name no está registrado o faltan argumentos requeridos.
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
