import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.conversations.schemas import ConversationalRequest
from app.conversations.service import ConversationsService
from app.core.security import AuthenticatedUser


@pytest.fixture
def mock_repo():
    return AsyncMock()

@pytest.fixture
def mock_intel_service():
    return AsyncMock()

@pytest.fixture
def mock_cash_service():
    service = AsyncMock()
    service.repo = AsyncMock()
    return service

@pytest.fixture
def mock_credit_service():
    service = AsyncMock()
    service.repo = AsyncMock()
    return service

@pytest.fixture
def mock_goals_service():
    service = AsyncMock()
    service.repo = AsyncMock()
    return service

@pytest.fixture
def mock_obl_service():
    service = AsyncMock()
    service.repo = AsyncMock()
    return service


@pytest.fixture
def conversations_service(mock_repo, mock_intel_service, mock_cash_service, mock_credit_service, mock_goals_service, mock_obl_service):
    service = ConversationsService(
        session=AsyncMock(),
        intel_service=mock_intel_service,
        cash_service=mock_cash_service,
        goals_service=mock_goals_service,
        obl_service=mock_obl_service,
        credit_service=mock_credit_service
    )
    service.repo = mock_repo
    return service


@pytest.mark.asyncio
async def test_handle_message_deduplication(conversations_service, mock_repo):
    user_id = uuid.uuid4()
    auth_user = AuthenticatedUser(user_id=str(user_id))
    trace_id = uuid.uuid4()
    req = ConversationalRequest(message="Hola", channel="api", external_message_id="ext-123")
    
    mock_repo.get_message_by_external_id.return_value = {
        "response_data": {"response_text": "Ya te respondí", "intent": "ask_balance", "status": "completed", "trace_id": str(trace_id)}
    }
    
    resp = await conversations_service.handle_message(auth_user, req, trace_id)
    assert resp.response_text == "Ya te respondí"
    mock_repo.get_message_by_external_id.assert_called_once_with("api", "ext-123")


@pytest.mark.asyncio
@patch("app.conversations.service.gemini_client")
async def test_handle_message_read_intent(mock_gemini, conversations_service, mock_intel_service, mock_repo):
    user_id = uuid.uuid4()
    auth_user = AuthenticatedUser(user_id=str(user_id))
    trace_id = uuid.uuid4()
    msg_id = uuid.uuid4()
    req = ConversationalRequest(message="¿Cuánto tengo?", channel="api")
    
    mock_repo.get_message_by_external_id.return_value = None
    mock_repo.save_message.return_value = msg_id
    
    # Mock NLU output
    from app.conversations.schemas import ExtractedEntities, GeminiNLUOutput
    nlu_out = GeminiNLUOutput(
        intent="ask_balance",
        entities=ExtractedEntities(),
        confidence=0.9
    )
    metadata = {
        "model": "gemini-3.1-flash-lite", "success": True, "prompt_tokens": 10,
        "completion_tokens": 5, "total_tokens": 15, "latency_ms": 100
    }
    mock_gemini.extract_intent_and_entities.return_value = (nlu_out, metadata)
    
    # Mock Intel
    from unittest.mock import MagicMock
    mock_data = MagicMock()
    mock_data.model_dump.return_value = {"total_available_real": "1500.00"}
    mock_intel_service.get_balance.return_value = mock_data
    
    resp = await conversations_service.handle_message(auth_user, req, trace_id)
    
    assert resp.status == "completed"
    assert resp.intent == "ask_balance"
    assert "1500.00" in resp.response_text
    
    # Assert ai_run was saved
    mock_repo.save_ai_run.assert_called_once()
