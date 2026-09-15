"""
Cliente HTTP para AMC.Core.SysEdi.

Encapsula todas las llamadas a los endpoints de herramientas del agente
expuestos por el microservicio .NET AMC.Core.SysEdi.

Cada método corresponde a una herramienta que el agente puede solicitar.
Los endpoints devuelven 501 hasta que SysEdi los implemente; el cliente
gestiona esa respuesta como NotImplementedError para que el worker
escale la incidencia.

Routing de herramientas → endpoints SysEdi:
  edicom.*     → /api/EDIAgent/edicom/...
  erp.*        → /api/EDIAgent/erp/...
  om_was.*     → /api/EDIAgent/omwas/...
  filesystem.* → /api/EDIAgent/filesystem/...
"""

import structlog
import httpx
from app.config import get_settings
from app.core.ports.tool_executor_port import ExternalPendingError, ToolExecutorPort

logger = structlog.get_logger()

# Mapeo tool_name → (método HTTP, path relativo en SysEdi)
# Los argumentos con {} se sustituyen con el valor del argumento del mismo nombre.
TOOL_ROUTES: dict[str, tuple[str, str]] = {
    # ── Edicom ────────────────────────────────────────────────────────────────
    "edicom.check_message_status":  ("GET",  "/api/EDIAgent/edicom/message-status"),
    "edicom.get_message_details":   ("GET",  "/api/EDIAgent/edicom/message/{messageId}"),
    "edicom.resend_message":        ("POST", "/api/EDIAgent/edicom/message/{messageId}/resend"),
    "edicom.search_messages":       ("GET",  "/api/EDIAgent/edicom/messages"),

    # ── ERP / SAP ─────────────────────────────────────────────────────────────
    "erp.get_delivery":             ("GET",  "/api/EDIAgent/erp/delivery/{deliveryNumber}"),
    "erp.search_order":             ("GET",  "/api/EDIAgent/erp/order"),
    "erp.get_invoice":              ("GET",  "/api/EDIAgent/erp/invoice/{invoiceNumber}"),
    "erp.get_idoc_details":         ("GET",  "/api/EDIAgent/erp/idoc/{idocNumber}"),
    "erp.get_error_log":            ("GET",  "/api/EDIAgent/erp/error-log"),

    # ── OM / WAS ──────────────────────────────────────────────────────────────
    "om_was.get_process_status":    ("GET",  "/api/EDIAgent/omwas/process-status"),
    "om_was.get_error_logs":        ("GET",  "/api/EDIAgent/omwas/error-logs"),
    "om_was.list_stuck_files":      ("GET",  "/api/EDIAgent/omwas/stuck-files"),

    # ── Filesystem ────────────────────────────────────────────────────────────
    "filesystem.check_file_location": ("GET",  "/api/EDIAgent/filesystem/check"),
    "filesystem.list_files":          ("GET",  "/api/EDIAgent/filesystem/list"),
    "filesystem.get_file_age":        ("GET",  "/api/EDIAgent/filesystem/file-age"),
    "filesystem.read_file":           ("GET",  "/api/EDIAgent/filesystem/read"),
    "filesystem.write_file":          ("POST", "/api/EDIAgent/filesystem/write"),
    "filesystem.move_file":           ("POST", "/api/EDIAgent/filesystem/move"),

    # ── PCAE ──────────────────────────────────────────────────────────────────
    "pcae.create_entry":              ("POST", "/api/EDIAgent/pcae/create-entry"),

    # ── ERP / SAP — búsqueda por GLN ─────────────────────────────────────────
    "erp.search_partner_by_gln":      ("GET",  "/api/EDIAgent/erp/partner"),
}


class SysEdiClient(ToolExecutorPort):
    """
    Cliente HTTP para las herramientas del agente expuestas por AMC.Core.SysEdi.
    """

    def __init__(self):
        settings = get_settings()
        self._client = httpx.AsyncClient(
            base_url=settings.get_sysEdi.rstrip("/"),
            timeout=httpx.Timeout(60.0),
            verify=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """
        Llama al endpoint de SysEdi correspondiente a la herramienta solicitada.

        Raises:
            NotImplementedError: si SysEdi devuelve 501 (herramienta no implementada aún).
            ValueError: si el tool_name no está registrado.
        """
        if tool_name not in TOOL_ROUTES:
            raise ValueError(
                f"Tool '{tool_name}' no registrada en SysEdiClient. "
                f"Añadir en TOOL_ROUTES cuando SysEdi exponga el endpoint."
            )

        method, path_template = TOOL_ROUTES[tool_name]

        # Sustituir parámetros de ruta con los argumentos (ej: {deliveryNumber})
        try:
            path = path_template.format(**arguments)
        except KeyError as exc:
            raise ValueError(
                f"Argumento requerido {exc} no presente en arguments={arguments} "
                f"para el tool '{tool_name}'"
            ) from exc

        # Argumentos restantes que no forman parte de la ruta van como query params o body
        path_params_used = {
            key for key in arguments
            if f"{{{key}}}" in path_template
        }
        remaining_args = {k: v for k, v in arguments.items() if k not in path_params_used}

        log = logger.bind(tool=tool_name, method=method, path=path)
        log.debug("Calling SysEdi tool")

        if method == "GET":
            response = await self._client.get(path, params=remaining_args)
        elif method == "POST":
            response = await self._client.post(path, json=remaining_args)
        else:
            response = await self._client.request(method, path, json=remaining_args)

        # 501: SysEdi conoce la ruta pero no la ha implementado todavía.
        # 404: la ruta ni siquiera existe todavía en SysEdi (p. ej. mientras
        # EDIAgentController no se ha desplegado en un entorno concreto).
        # Se tratan igual: para el Worker, en ambos casos la herramienta
        # "no está disponible hoy", no es un fallo real de ejecución.
        if response.status_code in (404, 501):
            raise NotImplementedError(
                f"SysEdi: tool '{tool_name}' pendiente de implementar "
                f"({method} {path}, HTTP {response.status_code})"
            )

        # 425 Too Early (RFC 8470): la herramienta se ejecutó, pero su
        # resultado depende de una condición externa todavía no cumplida
        # (p. ej. Edicom todavía no ha terminado de reprocesar un mensaje).
        # SysEdi puede opcionalmente indicar una estimación de espera en el
        # cuerpo de la respuesta: {"retry_after_minutes": N}.
        if response.status_code == 425:
            retry_after_minutes = None
            try:
                retry_after_minutes = response.json().get("retry_after_minutes")
            except Exception:
                pass
            raise ExternalPendingError(
                f"SysEdi: tool '{tool_name}' depende de una condición externa "
                f"todavía no cumplida ({method} {path})",
                retry_after_minutes=retry_after_minutes,
            )

        response.raise_for_status()

        try:
            return response.json()
        except Exception:
            return {"raw": response.text}
