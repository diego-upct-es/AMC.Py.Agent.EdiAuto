"""Puerto para el buzón de correo EDI (AMC.Core.Sys.Mailing)."""

from abc import ABC, abstractmethod
from datetime import datetime

from app.models.email_message import ParsedEmail


class MailboxPort(ABC):

    @abstractmethod
    async def get_messages(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        include_attachments: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def get_message_raw(self, message_id: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    async def mark_as_read(self, message_id: str, is_read: bool = True) -> None:
        raise NotImplementedError

    @abstractmethod
    async def move_message(self, message_id: str, destination_folder_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def send_email(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        html_body: str | None = None,
        attachments: list[tuple[str, str, bytes]] | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def create_folder(self, display_name: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def parse_eml(self, raw_eml: bytes, message_id: str) -> ParsedEmail:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
