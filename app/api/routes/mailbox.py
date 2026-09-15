import httpx
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_mailbox_port
from app.core.ports.mailbox_port import MailboxPort
from app.core.services.mailbox_service import MailboxService

router = APIRouter()


def _get_mailbox_service(
    mailing: MailboxPort = Depends(get_mailbox_port),
) -> MailboxService:
    return MailboxService(mailing)


@router.get(
    "",
    summary="Leer mensajes del buzón EDI",
)
async def get_mailbox_messages(
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
    service: MailboxService = Depends(_get_mailbox_service),
):
    """
    Consulta a demanda el buzón EDI vía SysMailing (GET /messages).
    No dispara el procesamiento de incidencias — solo devuelve los mensajes.
    """
    try:
        return await service.get_messages(from_dt, to_dt, page, page_size)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"SysMailing respondió con error: {exc.response.text}",
        )
