from pydantic import BaseModel
from datetime import datetime


class EmailAttachmentMeta(BaseModel):
    id: str
    name: str
    content_type: str
    size: int | None = None


class ParsedEmail(BaseModel):
    message_id: str
    subject: str
    from_email: str
    to: list[str] = []
    cc: list[str] = []
    received_datetime: datetime | None = None
    body_text: str = ""
    body_html: str = ""
    has_attachments: bool = False
    attachments_meta: list[EmailAttachmentMeta] = []
    attachments_text: list[str] = []
    conversation_id: str = ""
