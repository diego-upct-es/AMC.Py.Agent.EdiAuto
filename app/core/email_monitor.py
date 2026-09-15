"""
Monitor de buzón EDI.

Responsabilidad: detectar correos nuevos y enviarlos al procesador.

Modo de operación: polling. Consulta periódica cada EMAIL_POLL_INTERVAL_SECONDS.
Filtra correos de las últimas 24h y comprueba idempotencia en BD.

SysMailing ya no admite SSE (/api/Notifications/{mailbox}/stream) — el único
modo soportado es el polling periódico.
"""

import asyncio
import structlog
from datetime import datetime, timezone, timedelta

from app.config import get_settings
from app.core.ports.incident_repository_port import IncidentRepositoryPort
from app.core.ports.mailbox_port import MailboxPort

logger = structlog.get_logger()


class EmailMonitor:

    def __init__(
        self,
        mailing: MailboxPort,
        process_callback,
        repository: IncidentRepositoryPort,
    ):
        self._mailing = mailing
        self._process = process_callback
        self._repository = repository
        self._running = False

    async def start(self) -> None:
        self._running = True
        await self._run_polling()

    def stop(self) -> None:
        self._running = False

    # ── MODO POLLING ──────────────────────────────────────────────────────────

    async def _run_polling(self) -> None:
        settings = get_settings()
        interval = settings.email_poll_interval_seconds
        log = logger.bind(mode="polling", interval_s=interval, mailbox=settings.mailing_edi_mailbox)
        log.info("Email monitor started in polling mode")

        while self._running:
            try:
                await self._poll_once(log)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("Polling cycle error", error=str(exc))
            await asyncio.sleep(interval)

    async def _poll_once(self, log) -> None:
        now = datetime.now(timezone.utc)
        from_dt = now - timedelta(hours=24)

        response = await self._mailing.get_messages(
            from_dt=from_dt, to_dt=now, page=1, page_size=50
        )

        tag = response.get("tag", {})
        # SysMailing devuelve PagedResult: { pageData: [...], ... }
        messages = (
            tag.get("pageData", []) if isinstance(tag, dict) else
            (tag if isinstance(tag, list) else [])
        )

        for msg in messages:
            await self._dispatch_if_new(msg)

    # ── Helper común ──────────────────────────────────────────────────────────

    async def _dispatch_if_new(self, message_metadata: dict) -> None:
        message_id = message_metadata.get("id") or message_metadata.get("messageId", "")
        if not message_id:
            return

        already_processed = await self._repository.is_email_already_processed(message_id)
        if not already_processed:
            # Lanzar procesamiento en background para no bloquear el monitor
            asyncio.create_task(self._safe_process(message_metadata))

    async def _safe_process(self, message_metadata: dict) -> None:
        try:
            await self._process(message_metadata)
        except Exception as exc:
            logger.error(
                "Unhandled error processing email",
                message_id=message_metadata.get("id"),
                error=str(exc),
            )
