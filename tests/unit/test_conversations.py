import uuid
from datetime import UTC
from unittest.mock import AsyncMock, patch

import pytest

from app.conversations.schemas import ConversationalRequest
from app.conversations.service import ConversationsService
from app.core.security import AuthenticatedIdentity


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
def mock_transfers_service():
    from unittest.mock import AsyncMock
    return AsyncMock()

@pytest.fixture
def conversations_service(mock_repo, mock_intel_service, mock_cash_service, mock_credit_service, mock_goals_service, mock_obl_service, mock_transfers_service):
    service = ConversationsService(
        session=AsyncMock(),
        intel_service=mock_intel_service,
        cash_service=mock_cash_service,
        goals_service=mock_goals_service,
        obl_service=mock_obl_service,
        credit_service=mock_credit_service,
        transfers_service=mock_transfers_service
    )
    service.repo = mock_repo
    return service


@pytest.mark.asyncio
async def test_handle_message_deduplication(conversations_service, mock_repo):
    user_id = uuid.uuid4()
    AuthenticatedIdentity(user_id=str(user_id))
    trace_id = uuid.uuid4()
    req = ConversationalRequest(message="Hola", channel="api", external_message_id="ext-123")
    
    mock_repo.get_message_by_external_id.return_value = {
        "response_data": {"response_text": "Ya te respondí", "intent": "ask_balance", "status": "completed", "trace_id": str(trace_id)}
    }
    
    resp = await conversations_service.handle_message(user_id, req, trace_id)
    assert resp.response_text == "Ya te respondí"
    mock_repo.get_message_by_external_id.assert_called_once_with("api", "ext-123")


@pytest.mark.asyncio
@patch("app.conversations.service.gemini_client")
async def test_handle_message_read_intent(mock_gemini, conversations_service, mock_intel_service, mock_repo):
    user_id = uuid.uuid4()
    AuthenticatedIdentity(user_id=str(user_id))
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
    
    resp = await conversations_service.handle_message(user_id, req, trace_id)
    
    assert resp.status == "completed"
    assert resp.intent == "ask_balance"
    assert "1500.00" in resp.response_text
    
    # Assert ai_run was saved
    mock_repo.save_ai_run.assert_called_once()

@pytest.mark.asyncio
async def test_pending_action_confirm_natural(conversations_service, mock_repo, mock_cash_service):
    user_id = uuid.uuid4()
    AuthenticatedIdentity(user_id=str(user_id))
    trace_id = uuid.uuid4()
    
    action_id = uuid.uuid4()
    command_id = uuid.uuid4()
    req = ConversationalRequest(message="sí lo confirmo", channel="api", pending_action_id=str(action_id))
    
    from datetime import datetime

    from app.conversations.schemas import PendingActionRead
    action = PendingActionRead(
        id=action_id, user_id=user_id, intent="create_income",
        data={"amount": 500, "account_id": str(uuid.uuid4())},
        missing_fields=None, status="awaiting_confirmation", command_id=command_id,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC), expires_at=datetime.now(UTC)
    )
    
    mock_repo.get_pending_action.return_value = action
    mock_repo.save_message.return_value = uuid.uuid4()
    
    resp = await conversations_service.handle_message(user_id, req, trace_id)
    assert resp.status == "completed"
    mock_cash_service.create_income.assert_called_once()
    mock_repo.update_pending_action_status.assert_called_with(action_id, "executed")

@pytest.mark.asyncio
async def test_pending_action_ambiguous(conversations_service, mock_repo):
    user_id = uuid.uuid4()
    AuthenticatedIdentity(user_id=str(user_id))
    trace_id = uuid.uuid4()
    action_id = uuid.uuid4()
    req = ConversationalRequest(message="tal vez", channel="api", pending_action_id=str(action_id))
    
    from datetime import datetime

    from app.conversations.schemas import PendingActionRead
    action = PendingActionRead(
        id=action_id, user_id=user_id, intent="create_income",
        data={"amount": 500}, missing_fields=None, status="awaiting_confirmation", command_id=uuid.uuid4(),
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC), expires_at=datetime.now(UTC)
    )
    mock_repo.get_pending_action.return_value = action
    mock_repo.save_message.return_value = uuid.uuid4()
    
    resp = await conversations_service.handle_message(user_id, req, trace_id)
    assert resp.status == "awaiting_confirmation"

@pytest.mark.asyncio
async def test_pending_action_fallback(conversations_service, mock_repo, mock_cash_service):
    user_id = uuid.uuid4()
    AuthenticatedIdentity(user_id=str(user_id))
    trace_id = uuid.uuid4()
    
    action_id = uuid.uuid4()
    command_id = uuid.uuid4()
    req = ConversationalRequest(message="sí", channel="api")
    
    from datetime import datetime

    from app.conversations.schemas import PendingActionRead
    action = PendingActionRead(
        id=action_id, user_id=user_id, intent="create_income",
        data={"amount": 500, "account_id": str(uuid.uuid4())},
        missing_fields=None, status="awaiting_confirmation", command_id=command_id,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC), expires_at=datetime.now(UTC)
    )
    
    mock_repo.get_message_by_external_id.return_value = None
    mock_repo.get_open_pending_actions.return_value = [action]
    mock_repo.get_pending_action.return_value = action
    mock_repo.save_message.return_value = uuid.uuid4()
    
    resp = await conversations_service.handle_message(user_id, req, trace_id)
    assert resp.status == "completed"
    mock_cash_service.create_income.assert_called_once()
    mock_repo.update_pending_action_status.assert_called_with(action_id, "executed")


@pytest.mark.asyncio
async def test_pending_action_confirm_unsupported_intent(conversations_service, mock_repo):
    user_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    
    action_id = uuid.uuid4()
    command_id = uuid.uuid4()
    req = ConversationalRequest(message="sí", channel="api", pending_action_id=str(action_id))
    
    from datetime import datetime

    from app.conversations.schemas import PendingActionRead
    action = PendingActionRead(
        id=action_id, user_id=user_id, intent="unsupported_intent",
        data={"amount": 500},
        missing_fields=None, status="awaiting_confirmation", command_id=command_id,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC), expires_at=datetime.now(UTC)
    )
    
    mock_repo.get_pending_action.return_value = action
    mock_repo.save_message.return_value = uuid.uuid4()
    
    resp = await conversations_service.handle_message(user_id, req, trace_id)
    assert resp.status == "error"
    assert "No se registró ningún movimiento" in resp.response_text
    
    mock_repo.update_pending_action_status.assert_called_once_with(action_id, "awaiting_confirmation", confirmation_message_id=mock_repo.save_message.return_value)

@pytest.mark.asyncio
async def test_pending_action_confirm_create_goal(conversations_service, mock_repo, mock_goals_service):
    user_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    action_id = uuid.uuid4()
    command_id = uuid.uuid4()
    req = ConversationalRequest(message="sí", channel="api", pending_action_id=str(action_id))
    from datetime import UTC, datetime

    from app.conversations.schemas import PendingActionRead
    action = PendingActionRead(
        id=action_id, user_id=user_id, intent="create_goal",
        data={"name": "Viaje", "target_amount": 2000000, "target_date": "2026-12-31"},
        missing_fields=None, status="awaiting_confirmation", command_id=command_id,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC), expires_at=datetime.now(UTC)
    )
    mock_repo.get_pending_action.return_value = action
    mock_repo.save_message.return_value = uuid.uuid4()
    resp = await conversations_service.handle_message(user_id, req, trace_id)
    assert resp.status == "completed"
    mock_goals_service.create_goal.assert_called_once()
    mock_repo.update_pending_action_status.assert_called_with(action_id, "executed")

@pytest.mark.asyncio
async def test_pending_action_confirm_create_goal_contribution(conversations_service, mock_repo, mock_goals_service):
    user_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    action_id = uuid.uuid4()
    command_id = uuid.uuid4()
    req = ConversationalRequest(message="sí", channel="api", pending_action_id=str(action_id))
    from datetime import UTC, datetime

    from app.conversations.schemas import PendingActionRead
    action = PendingActionRead(
        id=action_id, user_id=user_id, intent="create_goal_contribution",
        data={"amount": 100000, "account_id": str(uuid.uuid4()), "goal_id": str(uuid.uuid4())},
        missing_fields=None, status="awaiting_confirmation", command_id=command_id,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC), expires_at=datetime.now(UTC)
    )
    mock_repo.get_pending_action.return_value = action
    mock_repo.save_message.return_value = uuid.uuid4()
    resp = await conversations_service.handle_message(user_id, req, trace_id)
    assert resp.status == "completed"
    mock_goals_service.create_contribution.assert_called_once()
    mock_repo.update_pending_action_status.assert_called_with(action_id, "executed")

@pytest.mark.asyncio
async def test_pending_action_confirm_create_obligation(conversations_service, mock_repo, mock_obl_service):
    user_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    action_id = uuid.uuid4()
    command_id = uuid.uuid4()
    req = ConversationalRequest(message="sí", channel="api", pending_action_id=str(action_id))
    from datetime import UTC, datetime

    from app.conversations.schemas import PendingActionRead
    action = PendingActionRead(
        id=action_id, user_id=user_id, intent="create_obligation",
        data={"name": "Arriendo", "amount": 900000, "due_day": 15, "frequency": "monthly"},
        missing_fields=None, status="awaiting_confirmation", command_id=command_id,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC), expires_at=datetime.now(UTC)
    )
    mock_repo.get_pending_action.return_value = action
    mock_repo.save_message.return_value = uuid.uuid4()
    resp = await conversations_service.handle_message(user_id, req, trace_id)
    assert resp.status == "completed"
    mock_obl_service.create_obligation.assert_called_once()
    mock_repo.update_pending_action_status.assert_called_with(action_id, "executed")

@pytest.mark.asyncio
async def test_pending_action_confirm_create_obligation_payment(conversations_service, mock_repo, mock_obl_service):
    user_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    action_id = uuid.uuid4()
    command_id = uuid.uuid4()
    req = ConversationalRequest(message="sí", channel="api", pending_action_id=str(action_id))
    from datetime import UTC, datetime

    from app.conversations.schemas import PendingActionRead
    action = PendingActionRead(
        id=action_id, user_id=user_id, intent="create_obligation_payment",
        data={"amount": 900000, "account_id": str(uuid.uuid4()), "obligation_id": str(uuid.uuid4())},
        missing_fields=None, status="awaiting_confirmation", command_id=command_id,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC), expires_at=datetime.now(UTC)
    )
    mock_repo.get_pending_action.return_value = action
    mock_repo.save_message.return_value = uuid.uuid4()
    resp = await conversations_service.handle_message(user_id, req, trace_id)
    assert resp.status == "completed"
    mock_obl_service.create_payment.assert_called_once()
    mock_repo.update_pending_action_status.assert_called_with(action_id, "executed")

@pytest.mark.asyncio
async def test_pending_action_confirm_create_credit_card_payment(conversations_service, mock_repo, mock_credit_service):
    user_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    action_id = uuid.uuid4()
    uuid.uuid4()
    req = ConversationalRequest(message="sí", channel="api", pending_action_id=str(action_id))
    from datetime import UTC, datetime

    from app.conversations.schemas import PendingActionRead
    action = PendingActionRead(
        id=action_id,
        user_id=user_id,
        intent="create_credit_card_payment",
        data={"amount": 100, "credit_card_id": str(uuid.uuid4()), "account_id": str(uuid.uuid4())},
        status="awaiting_confirmation",
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC), expires_at=datetime.now(UTC)
    )
    
    mock_repo.get_pending_action.return_value = action
    mock_repo.save_message.return_value = uuid.uuid4()
    
    resp = await conversations_service.handle_message(user_id, req, trace_id)
    assert resp.status == "completed"
    mock_credit_service.create_payment.assert_called_once()
    mock_repo.update_pending_action_status.assert_called_with(action_id, "executed")

@pytest.mark.asyncio
@patch("app.conversations.service.gemini_client")
async def test_pending_action_clarification_merge(mock_gemini, conversations_service, mock_repo):
    user_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    action_id = uuid.uuid4()
    req = ConversationalRequest(message="Nequi", channel="api", pending_action_id=str(action_id))
    from datetime import UTC, datetime

    from app.conversations.schemas import PendingActionRead
    action = PendingActionRead(
        id=action_id, user_id=user_id, intent="create_expense",
        data={"amount": 50000, "category_id": str(uuid.uuid4()), "_category_name": "Almuerzo"},
        missing_fields={"account_id": "¿Desde qué cuenta hiciste el movimiento?"},
        status="awaiting_clarification", command_id=None,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC), expires_at=datetime.now(UTC)
    )
    mock_repo.get_pending_action.return_value = action
    mock_repo.get_open_pending_actions.return_value = [action]
    mock_repo.save_message.return_value = uuid.uuid4()

    from app.conversations.schemas import ExtractedEntities, GeminiNLUOutput
    nlu_out = GeminiNLUOutput(intent="unknown", entities=ExtractedEntities(account="Nequi"), confidence=1.0)
    mock_gemini.extract_intent_and_entities.return_value = (nlu_out, {"model": "gemini-1.5-flash", "success": True, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "latency_ms": 10})

    # mock accounts to allow resolution
    from unittest.mock import MagicMock
    mock_account = MagicMock()
    mock_account.id = uuid.uuid4()
    mock_account.name = "Nequi"
    conversations_service.cash_service.account_repo.list_by_user.return_value = [mock_account]

    resp = await conversations_service.handle_message(user_id, req, trace_id)
    assert resp.status == "awaiting_confirmation"
    assert "Voy a registrar un gasto de $50000" in resp.response_text
    mock_repo.update_pending_action.assert_called_once()

@pytest.mark.asyncio
@patch("app.conversations.service.gemini_client")
async def test_pending_action_clarification_intent_change(mock_gemini, conversations_service, mock_repo):
    user_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    action_id = uuid.uuid4()
    req = ConversationalRequest(message="Mejor crea una meta Viaje por 1 millón", channel="api", pending_action_id=str(action_id))
    from datetime import UTC, datetime

    from app.conversations.schemas import PendingActionRead
    action = PendingActionRead(
        id=action_id, user_id=user_id, intent="create_expense",
        data={"amount": 50000},
        missing_fields={"account_id": "¿Desde qué cuenta hiciste el movimiento?"},
        status="awaiting_clarification", command_id=None,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC), expires_at=datetime.now(UTC)
    )
    mock_repo.get_pending_action.return_value = action
    mock_repo.get_open_pending_actions.return_value = [action]
    mock_repo.save_message.return_value = uuid.uuid4()
    
    from unittest.mock import MagicMock
    mock_new_action = MagicMock()
    mock_new_action.id = uuid.uuid4()
    mock_repo.create_pending_action.return_value = mock_new_action

    from app.conversations.schemas import ExtractedEntities, GeminiNLUOutput
    nlu_out = GeminiNLUOutput(intent="create_goal", entities=ExtractedEntities(amount=1000000, goal="Viaje"), confidence=1.0)
    mock_gemini.extract_intent_and_entities.return_value = (nlu_out, {"model": "gemini-1.5-flash", "success": True, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "latency_ms": 10})

    resp = await conversations_service.handle_message(user_id, req, trace_id)
    assert resp.status == "awaiting_confirmation"
    assert "Entendido. Cancelé el registro anterior." in resp.response_text
    mock_repo.update_pending_action_status.assert_called_with(action_id, "cancelled")
    mock_repo.create_pending_action.assert_called_once()
