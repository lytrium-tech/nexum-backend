import logging
import time
from typing import Any

from google import genai
from google.genai import types

from app.conversations.schemas import ExtractedEntities, GeminiNLUOutput
from app.core.config import settings

logger = logging.getLogger(__name__)


class GeminiClientError(Exception):
    """Excepción general para errores del cliente de Gemini."""
    pass


class GeminiClient:
    """Wrapper para interactuar con Google Gemini API usando Structured Outputs."""

    def __init__(self):
        # El SDK de google-genai espera api_key o la lee de GOOGLE_API_KEY.
        # Nosotros usamos GEMINI_API_KEY en config.
        api_key = settings.GEMINI_API_KEY.get_secret_value()
        if not api_key and not settings.is_development:
            # En modo dev permitimos iniciar la app sin key, pero fallará al usarse si no está mockeada.
            pass

        self.client = genai.Client(api_key=api_key) if api_key else None
        self.model = settings.GEMINI_MODEL
        self.fallback_model = getattr(settings, "GEMINI_FALLBACK_MODEL", "gemini-1.5-flash")
        self.timeout = getattr(settings, "GEMINI_TIMEOUT_SECONDS", 10)

    def extract_intent_and_entities(
        self,
        message: str,
        system_prompt: str,
        retries: int = 1
    ) -> tuple[GeminiNLUOutput, dict[str, Any]]:
        """
        Envía un mensaje a Gemini para extraer NLU y devuelve el resultado parseado
        por Pydantic, junto con metadatos de la corrida (tokens, latency, model_used).
        """
        import os
        if os.environ.get("MOCK_GEMINI", "").strip().lower() == "true":
            msg = message.lower()
            if "cuánto dinero tengo" in msg:
                nlu = GeminiNLUOutput(intent="ask_balance", entities=ExtractedEntities(), confidence=1.0)
            elif "cuánto dinero libre" in msg:
                nlu = GeminiNLUOutput(intent="ask_free_money", entities=ExtractedEntities(), confidence=1.0)
            elif "gasté 20 en comida desde nequi" in msg:
                nlu = GeminiNLUOutput(intent="create_expense", entities=ExtractedEntities(amount=20, category="comida", account="nequi"), confidence=1.0)
            elif "tarjeta ambigua" in msg:
                nlu = GeminiNLUOutput(intent="create_credit_card_purchase", entities=ExtractedEntities(amount=100, credit_card="tarjeta"), confidence=1.0)
            else:
                nlu = GeminiNLUOutput(intent="unknown", entities=ExtractedEntities(), confidence=1.0)
            return nlu, {"success": True, "model": "mock", "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "latency_ms": 10}

        if not self.client:
            raise GeminiClientError("Gemini API Key no configurada.")

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=GeminiNLUOutput,
            temperature=0.0,
        )

        attempts = 0
        current_model = self.model

        while attempts <= retries:
            start_time = time.time()
            try:
                response = self.client.models.generate_content(
                    model=current_model,
                    contents=[message],
                    config=config
                )
                
                latency_ms = int((time.time() - start_time) * 1000)
                
                # Acceder al JSON retornado y validarlo
                # El SDK con response_schema debería retornar un JSON estructurado.
                raw_text = response.text
                if not raw_text:
                    raise ValueError("Respuesta vacía de Gemini")
                
                # Validar con Pydantic
                nlu_output = GeminiNLUOutput.model_validate_json(raw_text)
                
                metadata = {
                    "model": current_model,
                    "prompt_tokens": response.usage_metadata.prompt_token_count if response.usage_metadata else 0,
                    "completion_tokens": response.usage_metadata.candidates_token_count if response.usage_metadata else 0,
                    "total_tokens": response.usage_metadata.total_token_count if response.usage_metadata else 0,
                    "latency_ms": latency_ms,
                    "success": True,
                }
                
                return nlu_output, metadata

            except Exception as e:
                attempts += 1
                logger.warning(f"Error con Gemini ({current_model}) intento {attempts}: {str(e)}")
                if attempts > retries:
                    if current_model == self.model and self.fallback_model:
                        logger.info(f"Usando modelo de fallback: {self.fallback_model}")
                        current_model = self.fallback_model
                        attempts = 0 # reset retries for fallback
                        continue
                    raise GeminiClientError(f"Fallo al comunicarse con Gemini después de varios intentos: {str(e)}")

        raise GeminiClientError("Fallo desconocido al interactuar con Gemini.")


# Instancia global (singleton pattern)
gemini_client = GeminiClient()
