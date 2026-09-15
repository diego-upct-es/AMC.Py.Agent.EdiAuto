"""
Cliente para Azure OpenAI, vía el endpoint unificado de un proyecto de Azure AI Foundry.

Autenticación: API Key simple (Bearer token), igual que OracleAIClient.
El endpoint (.../openai/v1) es compatible con el formato estándar de chat
completions, así que la petición y la respuesta tienen el mismo formato que
Oracle — este cliente es prácticamente un calco de OracleAIClient.

El nombre de implementación (deployment) desplegado en Foundry se pasa como
"model" en el payload, igual que el nombre de modelo de Oracle.
"""

import json
import structlog
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import get_settings
from app.core.ports.ai_port import AIPort

logger = structlog.get_logger()


class AzureOpenAIClient(AIPort):

    def __init__(self):
        settings = get_settings()
        self._endpoint = settings.azure_openai_endpoint.rstrip("/")
        self._api_key = settings.azure_openai_api_key
        self._deployment = settings.azure_openai_deployment
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),  # La IA puede tardar en responder
        )

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    )
    async def chat(self, prompt: str) -> str:
        """
        Llama a Azure OpenAI con un prompt y devuelve la respuesta como string.
        Cada llamada es stateless: el prompt contiene todo el contexto necesario.
        """
        # Sin "temperature": este modelo solo admite el valor por defecto (1),
        # rechaza cualquier otro con "unsupported_value" (confirmado probando
        # el endpoint directamente). Tampoco admite "max_tokens", solo
        # "max_completion_tokens" — mismo tipo de restricción.
        payload = {
            "model": self._deployment,
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": 2000,
        }

        log = logger.bind(deployment=self._deployment)
        log.debug("Calling Azure OpenAI")

        response = await self._client.post(
            f"{self._endpoint}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()

        result = response.json()
        content = result["choices"][0]["message"]["content"]
        log.debug("Azure OpenAI response received", length=len(content))
        return content

    async def analyze_json(self, prompt: str) -> dict:
        """
        Llama a la IA y parsea la respuesta como JSON.
        Limpia posibles bloques markdown que el modelo pueda añadir.
        Lanza ValueError si la respuesta no es JSON válido.
        """
        raw = await self.chat(prompt)

        # Limpiar markdown si el modelo lo añade (ej: ```json ... ```)
        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error(
                "Azure OpenAI response is not valid JSON",
                response_preview=raw[:300],
                error=str(exc),
            )
            raise ValueError(f"Azure OpenAI no devolvió JSON válido: {exc}") from exc
