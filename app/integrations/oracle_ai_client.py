"""
Cliente para Oracle Generative AI.

Autenticación: API Key simple (Bearer token).
El endpoint y el modelo son configurables vía variables de entorno
para facilitar el cambio de modelo sin tocar código.

Nota sobre el formato de la API:
  Oracle Generative AI expone un endpoint compatible con el estándar
  de chat completions (similar a OpenAI). El path exacto puede variar
  según la región y configuración del tenant Oracle Cloud.
  Ajustar ORACLE_AI_ENDPOINT si el path de la URL es diferente.

Modelos probados: documentar aquí a medida que se prueben.
  - [pendiente: probar modelos disponibles y anotar el seleccionado]
"""

import json
import structlog
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import get_settings
from app.core.ports.ai_port import AIPort

logger = structlog.get_logger()


class OracleAIClient(AIPort):

    def __init__(self):
        settings = get_settings()
        self._endpoint = settings.oracle_ai_endpoint.rstrip("/")
        self._api_key = settings.oracle_ai_api_key
        self._model = settings.oracle_ai_model
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
        Llama a Oracle Generative AI con un prompt y devuelve la respuesta como string.
        Cada llamada es stateless: el prompt contiene todo el contexto necesario.
        """
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,   # Baja temperatura → respuestas más determinísticas
            "max_tokens": 2000,
        }

        log = logger.bind(model=self._model)
        log.debug("Calling Oracle AI")

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
        log.debug("Oracle AI response received", length=len(content))
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
                "Oracle AI response is not valid JSON",
                response_preview=raw[:300],
                error=str(exc),
            )
            raise ValueError(f"Oracle AI no devolvió JSON válido: {exc}") from exc
