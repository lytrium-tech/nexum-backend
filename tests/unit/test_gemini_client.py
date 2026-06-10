from unittest.mock import MagicMock, patch

import pytest

from app.conversations.schemas import GeminiNLUOutput
from app.integrations.gemini_client import GeminiClient, GeminiClientError


# Mock config
@pytest.fixture(autouse=True)
def mock_settings():
    with patch("app.integrations.gemini_client.settings") as mock_settings:
        mock_settings.GEMINI_API_KEY.get_secret_value.return_value = "fake_key"
        mock_settings.GEMINI_MODEL = "gemini-3.1-flash-lite"
        mock_settings.GEMINI_FALLBACK_MODEL = "gemini-3.5-flash"
        mock_settings.GEMINI_TIMEOUT_SECONDS = 10
        yield mock_settings

@pytest.fixture
def gemini_client():
    with patch("app.integrations.gemini_client.genai.Client"):
        client = GeminiClient()
        # Mock client inside the instance
        client.client = MagicMock()
        return client

def test_extract_intent_and_entities_success(gemini_client):
    # Setup mock response
    mock_response = MagicMock()
    mock_response.text = '{"intent": "ask_balance", "entities": {}, "confidence": 0.95}'
    mock_response.usage_metadata.prompt_token_count = 10
    mock_response.usage_metadata.candidates_token_count = 5
    mock_response.usage_metadata.total_token_count = 15
    
    gemini_client.client.models.generate_content.return_value = mock_response

    output, metadata = gemini_client.extract_intent_and_entities("¿Cuánto tengo?", "system prompt")

    assert isinstance(output, GeminiNLUOutput)
    assert output.intent == "ask_balance"
    assert metadata["success"] is True
    assert metadata["model"] == "gemini-3.1-flash-lite"
    assert metadata["total_tokens"] == 15

def test_extract_intent_invalid_json_fallback(gemini_client):
    # First call fails with invalid JSON, second call (fallback) succeeds
    mock_response_fail = MagicMock()
    mock_response_fail.text = "This is not JSON"
    
    mock_response_success = MagicMock()
    mock_response_success.text = '{"intent": "ask_debt", "entities": {}, "confidence": 0.8}'
    
    gemini_client.client.models.generate_content.side_effect = [
        mock_response_fail, # Attempt 1: invalid json -> Pydantic ValidationError
        mock_response_fail, # Attempt 2: retry on primary model
        mock_response_success # Attempt 1 on fallback model
    ]

    output, metadata = gemini_client.extract_intent_and_entities("¿Cuánto debo?", "system prompt", retries=1)

    assert output.intent == "ask_debt"
    assert metadata["model"] == "gemini-3.5-flash" # Used fallback

def test_extract_intent_timeout_or_error(gemini_client):
    gemini_client.client.models.generate_content.side_effect = Exception("API Timeout")

    with pytest.raises(GeminiClientError, match="Fallo al comunicarse"):
        gemini_client.extract_intent_and_entities("Hola", "prompt", retries=0)
