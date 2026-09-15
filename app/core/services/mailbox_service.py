"""
Servicio de consulta del buzón EDI.

Encapsula la llamada a SysMailing para app/api/routes/mailbox.py.
Depende de MailboxPort (puerto), no de MailingClient directamente.
No crea su propia instancia: la recibe inyectada para reutilizar
la única instancia que vive en app.state (ver app/main.py lifespan).
"""

from datetime import datetime

from app.core.ports.mailbox_port import MailboxPort


class MailboxService:

    def __init__(self, mailing: MailboxPort):
        self._mailing = mailing

    async def get_messages(
        self,
        from_dt: datetime | None,
        to_dt: datetime | None,
        page: int,
        page_size: int,
    ) -> dict:
        return await self._mailing.get_messages(
            from_dt=from_dt, to_dt=to_dt, page=page, page_size=page_size
        )
