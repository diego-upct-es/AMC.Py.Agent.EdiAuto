"""
Endpoint genérico de recepción de texto plano / JSON.

Pensado para que sistemas externos (ej. una app que vuelca logs de sus
procesos a un .txt en una carpeta compartida) puedan mandar ese contenido
directamente por HTTP, sin que el agente tenga que leer archivos compartidos.
Reutilizable para cualquier otro tipo de texto plano que se quiera analizar.

De momento solo recibe y confirma — el análisis por el agente (LLM) se añadirá
más adelante, cuando haya acceso a la API correspondiente.
"""

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

logger = structlog.get_logger()

router = APIRouter()


class RawInputRequest(BaseModel):
    source: str
    content: str


class RawInputResponse(BaseModel):
    received: bool


@router.post(
    "",
    response_model=RawInputResponse,
    summary="Recibir texto plano genérico (logs u otro contenido) para análisis futuro",
)
async def receive_raw_input(payload: RawInputRequest) -> RawInputResponse:
    logger.info(
        "Raw input received",
        source=payload.source,
        content_length=len(payload.content),
    )
    return RawInputResponse(received=True)
