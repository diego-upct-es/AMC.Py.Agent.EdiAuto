"""
Ejecutor de un paso del plan de acción.

Responsabilidad: recibe una acción del plan (tool + arguments) y la ejecuta
llamando al endpoint correspondiente de AMC.Core.SysEdi.

No contiene lógica de negocio — solo routing y manejo de errores.
"""

import structlog

from app.core.ports.tool_executor_port import ExternalPendingError, ToolExecutorPort
from app.models.incident import ActionResult

logger = structlog.get_logger()


class ActionExecutor:

    def __init__(self, sysedi_client: ToolExecutorPort):
        self._sysedi = sysedi_client

    async def execute_step(self, action: dict) -> ActionResult:
        """
        Ejecuta una acción individual del plan.
        Devuelve un ActionResult con success=True/False y el resultado o el error.
        """
        tool_name = action.get("tool", "")
        arguments = action.get("arguments", {})
        log = logger.bind(tool=tool_name, order=action.get("order"))

        try:
            result = await self._sysedi.call_tool(tool_name, arguments)
            log.info("Step executed successfully")
            return ActionResult(action=action, success=True, result=result)

        except NotImplementedError as exc:
            log.warning("Tool not yet implemented in SysEdi", detail=str(exc))
            return ActionResult(
                action=action,
                success=False,
                error=f"Herramienta '{tool_name}' pendiente de implementar en SysEdi.",
                error_type="not_implemented",
            )

        except ExternalPendingError as exc:
            log.info("Tool result depends on an external condition not yet met", detail=str(exc))
            return ActionResult(
                action=action,
                success=False,
                error=str(exc),
                error_type="external_pending",
                retry_after_minutes=exc.retry_after_minutes,
            )

        except ValueError as exc:
            log.error("Tool routing error", error=str(exc))
            return ActionResult(action=action, success=False, error=str(exc))

        except Exception as exc:
            log.error("Step execution failed", error=str(exc))
            return ActionResult(action=action, success=False, error=str(exc))
