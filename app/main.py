import asyncio
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.config import get_settings
from app.integrations.database import get_pool, close_pool
from app.integrations.mailing_client import MailingClient
from app.integrations.oracle_ai_client import OracleAIClient
from app.integrations.azure_openai_client import AzureOpenAIClient
from app.integrations.postgres_incident_repository import PostgresIncidentRepository
from app.integrations.sysedi_client import SysEdiClient
from app.core.agent import EDIAgent
from app.core.action_executor import ActionExecutor
from app.core.incident_processor import IncidentProcessor
from app.core.email_monitor import EmailMonitor
from app.core.worker import Worker
from app.integrations.servicebus import ServiceBusSender, ServiceBusReceiver
from app.api.routes import health, incidents, mailbox, raw_input

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # get_settings() dispara llamadas HTTP síncronas al SDK de Azure (Key Vault).
    # Si se ejecuta directamente aquí, bloquea el hilo del event loop y con él
    # el accept() del socket TCP de uvicorn. Se resuelve una vez en un hilo aparte;
    # @lru_cache hace que las llamadas siguientes (en los __init__ de los clientes
    # de más abajo) devuelvan el resultado ya cacheado sin volver a tocar Key Vault.
    settings = await asyncio.to_thread(get_settings)
    logger.info(
        "Starting AMC.Py.Agent.EdiAuto",
        environment=settings.environment,
        mailbox=settings.mailing_edi_mailbox,
    )

    # ── Base de datos ─────────────────────────────────────────────────────────
    await get_pool()
    logger.info("Database pool initialized")

    # ── Clientes ──────────────────────────────────────────────────────────────
    mailing = MailingClient()
    app.state.mailing = mailing  # única instancia — reutilizada por app/api/routes/mailbox.py
    ai_client = AzureOpenAIClient() if settings.ai_provider == "azure" else OracleAIClient()
    logger.info("AI provider seleccionado", provider=settings.ai_provider)
    sysedi = SysEdiClient()
    sb_sender = ServiceBusSender()
    sb_receiver = ServiceBusReceiver()

    # ── Componentes de negocio ────────────────────────────────────────────────
    repository = PostgresIncidentRepository()
    agent = EDIAgent(ai_client, repository)
    executor = ActionExecutor(sysedi)
    processor = IncidentProcessor(mailing, agent, sb_sender, repository)
    worker = Worker(agent, executor, mailing, sb_sender, sb_receiver, repository)

    # ── Asegurar carpeta "Procesados" en el buzón ─────────────────────────────
    try:
        await mailing.create_folder("Procesados")
    except Exception:
        pass  # SysMailing no crea duplicados

    # ── Arrancar monitor de correo y worker en background ─────────────────────
    monitor = EmailMonitor(mailing, processor.process, repository)
    monitor_task = asyncio.create_task(monitor.start())
    worker_task = asyncio.create_task(worker.start())
    logger.info("Email monitor and worker started")

    yield  # ── Microservicio activo ─────────────────────────────────────────

    # Todo lo que hay antes del yield sería parte de la fase de arranque de la app 
    # mientras que lo que hay después es parte de la fase de apagado de la app (shutdown)

    # ── Shutdown ordenado ─────────────────────────────────────────────────────
    logger.info("Shutting down AMC.Py.Agent.EdiAuto...")
    monitor.stop()
    worker.stop()
    for task in [monitor_task, worker_task]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await mailing.close()
    await ai_client.close()
    await sysedi.close()
    await sb_receiver.close()
    await close_pool()
    logger.info("Shutdown complete")


app = FastAPI(
    title="AMC.Py.Agent.EdiAuto",
    description="Agente IA para gestión automática de incidencias EDI",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/console",
    redoc_url="/redoc",
)

@app.get("/", tags=["Health"], summary="Información básica del servicio")
async def root() -> dict:
    return {
        "service": app.title,
        "description": app.description,
        "version": app.version,
        "docs": app.docs_url,
    }


app.include_router(health.router)
app.include_router(incidents.router, prefix="/api/incidents", tags=["Incidencias"])
app.include_router(mailbox.router, prefix="/api/mailbox", tags=["Buzón"])
app.include_router(raw_input.router, prefix="/api/raw-input", tags=["Raw Input"])
