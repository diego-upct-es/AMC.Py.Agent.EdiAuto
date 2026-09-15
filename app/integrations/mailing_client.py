"""
Cliente HTTP para AMC.Core.Sys.Mailing.

Contratos extraídos directamente del código fuente de SysMailing
(AMC.Core.Infraestructura.ClienteHttp.SysMailing/Api/Mailbox.cs y NotificationsController.cs).

Autenticación:
  - QA: BYPASS_AUTH=SI en SysMailing → sin autenticación (llamadas directas).
  - PRO: JWT Bearer Token de Azure AD (Entra ID). A implementar cuando
         se registre la app en Entra ID y se configure el acceso.

Notas de contrato importantes:
  - GET /messages filtra por rango de fechas (no por estado leído/no leído).
  - PATCH /flags: body NULL, isRead como query param.
  - POST /send/simple: multipart/form-data (NO json).

Manejo de errores:
  - SysMailing devuelve BaseResponse.Error(mensaje, httpStatusCode) para cualquier
    código distinto de 200 (ej. MailboxNotFoundException → 404). El campo "mensaje"
    del body describe la causa. _raise_for_status lo extrae y lo loggea antes de
    relanzar, para que quede constancia del motivo exacto en los logs de esta app.
"""

import email
import io
import structlog
import httpx
from datetime import datetime
from email import policy as email_policy
from pypdf import PdfReader

from app.config import get_settings
from app.core.ports.mailbox_port import MailboxPort
from app.models.email_message import ParsedEmail

logger = structlog.get_logger()


class MailingClient(MailboxPort):

    def __init__(self):
        settings = get_settings()
        self._mailbox = settings.mailing_edi_mailbox
        self._client = httpx.AsyncClient(
            base_url=settings.mailing_base_url.rstrip("/"),
            timeout=httpx.Timeout(60.0, read=120.0),
            verify=False,  # Igual que el HttpClientHandler de SysMailing (.NET)
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Comprueba el código HTTP y loggea el mensaje de error de SysMailing
        (BaseResponse.Error) antes de relanzar la excepción."""
        if response.status_code == 200:
            return
        mensaje = str(response.status_code)
        try:
            body = response.json()
            mensaje = body.get("mensaje") or body.get("message") or mensaje
        except Exception:
            pass
        logger.error(
            "SysMailing respondió con error",
            status_code=response.status_code,
            mensaje=mensaje,
            url=str(response.request.url),
        )
        response.raise_for_status()

    # ── Lectura de mensajes ───────────────────────────────────────────────────

    async def get_messages(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        include_attachments: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """
        GET /api/Mailbox/{mailbox}/messages
        Respuesta: BaseResponse con tag = PagedResult<EmailMessageBase>
        PagedResult: { pageData: [...], page, pageSize, hasMorePages }
        """
        params: dict = {
            "includeAttachments": str(include_attachments).lower(),
            "page": page,
            "pageSize": page_size,
        }
        if from_dt:
            params["from"] = from_dt.isoformat()
        if to_dt:
            params["to"] = to_dt.isoformat()

        response = await self._client.get(
            f"/api/Mailbox/{self._mailbox}/messages", params=params
        )
        self._raise_for_status(response)
        return response.json()

    async def get_message(self, message_id: str) -> dict:
        """
        GET /api/Mailbox/{mailbox}/messages/{messageId}
        Respuesta: BaseResponse con tag = EmailMessage
        (incluye Body y Attachments como metadatos, sin contenido binario)
        """
        response = await self._client.get(
            f"/api/Mailbox/{self._mailbox}/messages/{message_id}"
        )
        self._raise_for_status(response)
        return response.json()

    async def get_message_raw(self, message_id: str) -> bytes:
        """
        GET /api/Mailbox/{mailbox}/messages/{messageId}/raw
        Respuesta: bytes del fichero .eml (RFC 2822/MIME)
        """
        response = await self._client.get(
            f"/api/Mailbox/{self._mailbox}/messages/{message_id}/raw"
        )
        self._raise_for_status(response)
        return response.content

    async def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """
        GET /api/Mailbox/{mailbox}/messages/{messageId}/attachments/{attachmentId}
        Respuesta: bytes crudos del adjunto
        """
        response = await self._client.get(
            f"/api/Mailbox/{self._mailbox}/messages/{message_id}/attachments/{attachment_id}"
        )
        self._raise_for_status(response)
        return response.content

    # ── Gestión de estado ─────────────────────────────────────────────────────

    async def mark_as_read(self, message_id: str, is_read: bool = True) -> None:
        """
        PATCH /api/Mailbox/{mailbox}/messages/{messageId}/flags?isRead=true
        Body: null (no body — solo query param)
        """
        response = await self._client.patch(
            f"/api/Mailbox/{self._mailbox}/messages/{message_id}/flags",
            params={"isRead": str(is_read).lower()},
        )
        self._raise_for_status(response)

    async def move_message(self, message_id: str, destination_folder_id: str) -> None:
        """
        POST /api/Mailbox/{mailbox}/messages/{messageId}/move
        Body: { "destinationFolderId": "string" }
        Acepta IDs de Graph o nombres conocidos: inbox, drafts, sentitems, deleteditems
        También acepta el nombre de una carpeta personalizada (ej: "Procesados").
        """
        response = await self._client.post(
            f"/api/Mailbox/{self._mailbox}/messages/{message_id}/move",
            json={"destinationFolderId": destination_folder_id},
        )
        self._raise_for_status(response)

    # ── Envío ─────────────────────────────────────────────────────────────────

    async def send_email(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        html_body: str | None = None,
        attachments: list[tuple[str, str, bytes]] | None = None,
    ) -> None:
        """
        POST /api/Mailbox/{mailbox}/send/simple
        Content-Type: multipart/form-data (NO json — extraído de Mailbox.cs)

        Campos del form:
          To        → repetir por cada destinatario
          Cc        → repetir por cada CC
          Subject   → asunto
          Body      → cuerpo texto plano
          HtmlBody  → cuerpo HTML (opcional)
          Attachments → ficheros binarios (opcional)

        attachments: lista de (filename, content_type, bytes)
        """
        # httpx solo activa la codificación de formulario (urlencoded o
        # multipart) cuando `data` es un dict — con una lista de tuplas cae
        # en su ruta de "contenido crudo" y genera un stream síncrono, que
        # un AsyncClient no puede enviar ("Attempted to send an sync request
        # with an AsyncClient instance"). Los valores lista/tupla sí generan
        # campos repetidos (ej. varios "To"), igual que antes.
        data: dict[str, str | list[str]] = {
            "To": to,
            "Subject": subject,
            "Body": body,
        }
        if cc:
            data["Cc"] = cc
        if html_body:
            data["HtmlBody"] = html_body

        files: list = []
        if attachments:
            for filename, content_type, content in attachments:
                files.append(("Attachments", (filename, content, content_type)))
        else:
            # SysMailing exige multipart/form-data siempre (ver docstring),
            # pero httpx solo lo activa si `files` no está vacío — sin esto,
            # sin adjuntos reales se manda como urlencoded y SysMailing
            # responde 415. Campo vacío para forzar la codificación correcta.
            files.append(("__multipart_marker__", ("", b"")))

        response = await self._client.post(
            f"/api/Mailbox/{self._mailbox}/send/simple",
            data=data,
            files=files,
        )
        self._raise_for_status(response)

    # ── Carpetas ──────────────────────────────────────────────────────────────

    async def get_folders(self) -> dict:
        """GET /api/Mailbox/{mailbox}/folders"""
        response = await self._client.get(f"/api/Mailbox/{self._mailbox}/folders")
        self._raise_for_status(response)
        return response.json()

    async def create_folder(self, display_name: str) -> dict:
        """
        POST /api/Mailbox/{mailbox}/folders
        Body: { "displayName": "nombre" }
        SysMailing no crea duplicados si ya existe una carpeta con ese nombre.
        """
        response = await self._client.post(
            f"/api/Mailbox/{self._mailbox}/folders",
            json={"displayName": display_name},
        )
        self._raise_for_status(response)
        return response.json()

    # ── Parseo de .eml ────────────────────────────────────────────────────────

    def parse_eml(self, raw_eml: bytes, message_id: str) -> ParsedEmail:
        """
        Parsea un fichero .eml (RFC 2822) y extrae los campos relevantes.
        Usado después de get_message_raw().
        """
        msg = email.message_from_bytes(raw_eml, policy=email_policy.default)

        body_text = ""
        body_html = ""
        attachments_text: list[str] = []

        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue

                # Los adjuntos van embebidos en el propio .eml — no hace falta
                # una llamada aparte a get_attachment() para leerlos.
                if ct == "application/pdf":
                    extracted = self._extract_pdf_text(payload, part.get_filename())
                    if extracted:
                        attachments_text.append(extracted)
                    continue

                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace")
                if ct == "text/plain" and not body_text:
                    body_text = decoded
                elif ct == "text/html" and not body_html:
                    body_html = decoded
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                body_text = payload.decode(charset, errors="replace")

        from_header = msg.get("From", "")
        to_header = msg.get("To", "")
        cc_header = msg.get("Cc", "")

        return ParsedEmail(
            message_id=message_id,
            subject=str(msg.get("Subject", "")),
            from_email=from_header,
            to=[a.strip() for a in to_header.split(",") if a.strip()],
            cc=[a.strip() for a in cc_header.split(",") if a.strip()],
            body_text=body_text,
            body_html=body_html,
            attachments_text=attachments_text,
        )

    @staticmethod
    def _extract_pdf_text(payload: bytes, filename: str | None) -> str | None:
        """
        Extrae el texto de un adjunto PDF generado digitalmente.
        Devuelve None si no hay texto útil (ej. un PDF escaneado sin capa de
        texto — para eso haría falta OCR, no implementado aquí) o si el
        fichero no se puede leer.
        """
        try:
            reader = PdfReader(io.BytesIO(payload))
            text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception as exc:
            logger.warning("No se pudo leer el adjunto PDF", filename=filename, error=str(exc))
            return None
        if not text:
            return None
        return f"--- Adjunto: {filename or 'documento.pdf'} ---\n{text}"
