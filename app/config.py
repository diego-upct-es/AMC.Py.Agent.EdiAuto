import os
from functools import lru_cache
from typing import Tuple, Type

import structlog
from azure.identity import DefaultAzureCredential
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import (
    AzureKeyVaultSettingsSource,
    BaseSettings,
    PydanticBaseSettingsSource,
)

logger = structlog.get_logger()


class ConnectionStringsModel(BaseModel, frozen=True):
    """Nombre de cada campo == nombre del secreto en Key Vault tras el prefijo
    de entorno, ej. EdiAuto -> qa-ConnectionStrings--EdiAuto / pro-ConnectionStrings--EdiAuto.
    No usar alias salvo que el secreto ya exista con un nombre distinto al del campo:
    un alias que no coincide con el nombre real del secreto falla en silencio
    (el campo se queda vacío sin ningún error) — es lo que pasó con AzureServiceBus."""

    EdiAuto: SecretStr = Field(default=SecretStr(""))
    AzureServiceBus: SecretStr = Field(default=SecretStr(""))



def _cast_entorno(environment: str) -> str:
    """Prefijo de Key Vault por entorno: development→qa, production→pro, cualquier otro valor→dev."""
    if environment == "development" or environment == "qa":
        return "qa"
    elif environment == "production":
        return "pro"
    else:
        return "dev"


class Settings(BaseSettings):
    # ── Selección de proveedor de IA: "oracle" o "azure" ──────────────────────
    ai_provider: str = "oracle"

    # ── Oracle Generative AI ──────────────────────────────────────────────────
    oracle_ai_endpoint: str = ""
    oracle_ai_api_key: str = ""
    oracle_ai_model: str = ""

    # ── Azure OpenAI (vía Azure AI Foundry) ───────────────────────────────────
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""
    # TODO: temporal en .env por indicación de Fede (2026-08-13) — pasará a
    # Key Vault (qa-ConnectionStrings--AzureOpenAI) en cuanto exista el secreto.
    azure_openai_api_key: str = ""

    # ── AMC.Core.Sys.Mailing ─────────────────────────────────────────────────
    mailing_base_url: str = "https://sys-mailing-qa-private.msa.amcglobalco.com"
    mailing_edi_mailbox: str = "integrations_test@amcglobalco.com"

    # ── AMC.Core.SysEdi (herramientas del agente) ────────────────────────────
    sys_edi_url: str = "sys-edi-{entorno}-private.msa.amcglobalco.com"
    # Override manual y temporal, solo para pruebas puntuales contra el SysEdi
    # de otro entorno (p. ej. "pro") sin cambiar `environment` — que también
    # movería la base de datos y la cola de Service Bus a ese mismo entorno.
    sys_edi_entorno_override: str = ""

    ConnectionStrings: ConnectionStringsModel = Field(default_factory=ConnectionStringsModel)

    # ── Azure Service Bus ─────────────────────────────────────────────────────
    azure_servicebus_queue_name: str = "{entorno}.py.agent.events"

    # ── Base de Datos PostgreSQL ──────────────────────────────────────────────
    db_pool_size: int = 10

    # ── Configuración del sistema ─────────────────────────────────────────────
    email_poll_interval_seconds: int = 300
    internal_alert_email: str = ""
    environment: str = "development"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def get_sysEdi(self) -> str:
        entorno = self.sys_edi_entorno_override or _cast_entorno(self.environment)
        return self.sys_edi_url.format(entorno=entorno)

    @property
    def get_QueueName(self) -> str:
        return self.azure_servicebus_queue_name.format(entorno = _cast_entorno(self.environment))
    
    @property
    def database_url(self) -> str:
        return self.ConnectionStrings.EdiAuto.get_secret_value()

    @property
    def get_QueueConnString(self) -> str:
        return self.ConnectionStrings.AzureServiceBus.get_secret_value()

    @classmethod
    def settings_customise_sources(
        cls, settings_cls: Type[BaseSettings], init_settings: PydanticBaseSettingsSource, env_settings: PydanticBaseSettingsSource, dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:

        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            dotenv_settings,
        ]

        keyvault_name = os.getenv("KEYVAULT_NAME", "kv-int-com-002")
        entorno = _cast_entorno(os.getenv("ENVIRONMENT", "development"))
        try:
            sources.append(
                AzureKeyVaultSettingsSource(
                    settings_cls,
                    f"https://{keyvault_name}.vault.azure.net/",
                    DefaultAzureCredential(),
                    env_prefix=f"{entorno}-",
                )
            )
            logger.info("Key Vault habilitado", keyvault_name=keyvault_name, prefijo=f"{entorno}-")
        except Exception as e:
            logger.warning("Key Vault no disponible, se ignora", keyvault_name=keyvault_name, error=str(e))

        return tuple(sources)


@lru_cache()
def get_settings() -> Settings:
    """
    Carga la configuración (incluida la resolución de secretos en Key Vault)
    la primera vez que se necesita, no al importar el módulo.

    El SDK de Azure hace llamadas HTTP síncronas y bloqueantes; @lru_cache
    garantiza que Key Vault solo se consulta una vez por proceso. Llamar
    siempre a través de asyncio.to_thread(get_settings) cuando se invoque
    desde código async, para no bloquear el event loop en esa primera vez.
    """
    return Settings()
