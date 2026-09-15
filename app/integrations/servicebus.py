"""
Wrapper de Azure Service Bus.

Sender: encola mensajes desde IncidentProcessor.
Receiver: consumido por Worker. Mantiene la conexión abierta entre
llamadas a receive_one() en vez de reconectar en cada vuelta del bucle
— solo reconecta si una llamada falla con un error real. Actualiza
servicebus_state.listener_state en cada vuelta (heartbeat), para que
/health pueda distinguir "vivo sin tráfico" de "colgado".

Usa el mismo Azure Service Bus que los microservicios .NET de AMC
(PrcSd y otros usan AddMessageBrokerClient con el mismo connection string).
"""

import asyncio
import json
import structlog
from datetime import datetime, timedelta, timezone
from azure.servicebus.aio import ServiceBusClient
from azure.servicebus import ServiceBusMessage
from app.config import get_settings
from app.core.ports.message_queue_port import MessageQueueSenderPort, MessageQueueReceiverPort
from app.core.servicebus_state import listener_state

logger = structlog.get_logger()


class ServiceBusSender(MessageQueueSenderPort):
    """Envía mensajes a la cola de incidencias."""

    async def send(self, payload: dict, delay_seconds: int | None = None) -> None:
        settings = get_settings()
        if not settings.get_QueueConnString:
            logger.warning(
                "AZURE_SERVICEBUS_CONNECTION_STRING not configured — "
                "message NOT enqueued (set it to enable async worker processing)"
            )
            return

        async with ServiceBusClient.from_connection_string(
            settings.get_QueueConnString
        ) as client:
            async with client.get_queue_sender(
                settings.get_QueueName
            ) as sender:
                msg = ServiceBusMessage(
                    json.dumps(payload, ensure_ascii=False, default=str)
                )
                if delay_seconds:
                    scheduled_time = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
                    await sender.schedule_messages(msg, scheduled_time)
                    logger.debug(
                        "Message scheduled",
                        incident_id=payload.get("incident_id"),
                        step=payload.get("step_index"),
                        delay_seconds=delay_seconds,
                        queue=settings.get_QueueName,
                    )
                else:
                    await sender.send_messages(msg)
                    logger.debug(
                        "Message enqueued",
                        incident_id=payload.get("incident_id"),
                        step=payload.get("step_index"),
                        queue=settings.get_QueueName,
                    )


class ServiceBusReceiver(MessageQueueReceiverPort):
    """Recibe mensajes de la cola de incidencias (usado por Worker)."""

    def __init__(self):
        self._client: ServiceBusClient | None = None
        self._receiver = None

    async def _ensure_connected(self, settings) -> None:
        if self._receiver is not None:
            return
        self._client = ServiceBusClient.from_connection_string(
            settings.get_QueueConnString
        )
        self._receiver = self._client.get_queue_receiver(settings.get_QueueName)
        listener_state.mark_running()
        logger.info("Conectado a Service Bus", queue=settings.get_QueueName)

    async def _reset_connection(self) -> None:
        """Cierra y descarta la conexión actual para forzar una reconexión limpia."""
        for obj in (self._receiver, self._client):
            if obj is None:
                continue
            try:
                await obj.close()
            except Exception:
                pass
        self._receiver = None
        self._client = None

    async def receive_one(self, max_wait_seconds: int = 30) -> tuple[dict | None, object | None]:
        """
        Recibe un mensaje. Devuelve (payload_dict, raw_message).
        Devuelve (None, None) si no hay mensajes en el timeout, si la cola
        no está configurada, o si hubo un error de conexión (que se
        registra en listener_state y fuerza una reconexión en la próxima
        llamada).
        El caller debe llamar a complete(raw_message) cuando el paso termine con éxito.
        """
        settings = get_settings()
        if not settings.get_QueueConnString:
            return None, None

        try:
            await self._ensure_connected(settings)
            messages = await self._receiver.receive_messages(
                max_message_count=1, max_wait_time=max_wait_seconds
            )
        except Exception as exc:
            listener_state.mark_error(str(exc))
            logger.error("Error de conexión con Service Bus, reconectando", error=str(exc))
            await self._reset_connection()
            await asyncio.sleep(2)  # evita reintentos en bucle cerrado si la cola sigue caída
            return None, None

        # Late aunque no haya llegado nada: el bucle sigue vivo.
        listener_state.heartbeat()

        if not messages:
            return None, None

        msg = messages[0]
        try:
            payload = json.loads(str(msg))
            listener_state.mark_message_processed()
            return payload, (self._receiver, msg)
        except json.JSONDecodeError as exc:
            logger.error("Could not parse SB message", error=str(exc))
            await self._receiver.dead_letter_message(msg, reason="InvalidJSON")
            return None, None

    async def complete(self, receiver_and_msg: tuple) -> None:
        receiver, msg = receiver_and_msg
        await receiver.complete_message(msg)

    async def dead_letter(self, receiver_and_msg: tuple, reason: str) -> None:
        receiver, msg = receiver_and_msg
        await receiver.dead_letter_message(msg, reason=reason)

    async def close(self) -> None:
        """Cierra la conexión al apagar el proceso — llamado desde el lifespan."""
        await self._reset_connection()
        listener_state.mark_stopped()
