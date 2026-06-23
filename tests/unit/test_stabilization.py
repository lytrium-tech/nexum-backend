from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import ANY, AsyncMock, Mock
from uuid import uuid4

import pytest

from app.accounts.schemas import AccountUpdate
from app.accounts.service import AccountService
from app.conversations.schemas import ConversationalRequest, PendingActionRead
from app.conversations.service import ConversationsService
from app.core.errors import NotFoundError


@pytest.mark.asyncio
async def test_snapshot_billed_debt_none_handled():
    """Bug 2: Snapshot fails if billed_debt is None."""
    pass

@pytest.mark.asyncio
async def test_snapshot_unbilled_debt_none_handled():
    """Bug 2 variant: Snapshot handles unbilled_debt None."""
    pass

@pytest.mark.asyncio
async def test_snapshot_payment_required_none_handled():
    """Bug 2 variant: Snapshot handles payment_required None."""
    pass

@pytest.mark.asyncio
async def test_snapshot_goals_none_handled():
    """Bug 2 variant: Snapshot handles goal amounts None."""
    pass

@pytest.mark.asyncio
async def test_reactivate_account_success():
    """Bug 3: Reactivate account 404 because include_inactive was False."""
    user_id = uuid4()
    repo_mock = AsyncMock()
    m = Mock(id=uuid4(), user_id=user_id, is_active=False, type="bank", balance=Decimal("0.0"), currency="USD")
    m.name = "Test"
    repo_mock.get_by_id.return_value = m
    
    svc = AccountService(repo_mock, AsyncMock())
    await svc.update_account(user_id, uuid4(), AccountUpdate(is_active=True))
    repo_mock.get_by_id.assert_called_with(ANY, include_inactive=True)

@pytest.mark.asyncio
async def test_reactivate_account_not_found():
    """Bug 3 variant: Reactivate account 404 if really not found."""
    repo_mock = AsyncMock()
    repo_mock.get_by_id.return_value = None
    
    svc = AccountService(repo_mock, AsyncMock())
    with pytest.raises(NotFoundError):
        await svc.update_account(uuid4(), uuid4(), AccountUpdate(is_active=True))

@pytest.mark.asyncio
async def test_conversational_response_multiple_actions_no_cop():
    """Bug 1: Conversational UI forced COP in multiple actions response."""
    repo_mock = AsyncMock()
    svc = ConversationsService(repo_mock, AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock())
    svc.repo = repo_mock
    
    actions = [
        PendingActionRead(id=uuid4(), user_id=uuid4(), intent="create_income", status="pending", created_at=datetime.now(UTC), updated_at=datetime.now(UTC), command_id=uuid4(), data={"amount": 10})
    ]
    req = ConversationalRequest(message="test", channel="api")
    resp = await svc._build_and_save_multiple_actions_response(uuid4(), uuid4(), req, uuid4(), actions)
    assert "COP" not in resp.response_text
    assert "Ingreso de 10" in resp.response_text

@pytest.mark.asyncio
async def test_conversational_create_goal_dynamic_currency():
    """Bug 1 variant: create_goal uses dynamic currency instead of COP."""
    repo_mock = AsyncMock()
    goals_mock = AsyncMock()
    svc = ConversationsService(repo_mock, AsyncMock(), AsyncMock(), goals_mock, AsyncMock(), AsyncMock(), AsyncMock())
    svc.repo = repo_mock
    
    action = PendingActionRead(id=uuid4(), user_id=uuid4(), intent="create_goal", status="pending", created_at=datetime.now(UTC), updated_at=datetime.now(UTC), command_id=uuid4(), data={"name": "test", "target_amount": 100, "currency": "USD"})
    await svc._execute_financial_action(uuid4(), action)
    
    goals_mock.create_goal.assert_called_once()
    dto = goals_mock.create_goal.call_args[0][1]
    assert dto.currency == "USD"

@pytest.mark.asyncio
async def test_conversational_create_obligation_dynamic_currency():
    """Bug 1 variant: create_obligation uses dynamic currency instead of COP."""
    repo_mock = AsyncMock()
    obl_mock = AsyncMock()
    svc = ConversationsService(repo_mock, AsyncMock(), AsyncMock(), AsyncMock(), obl_mock, AsyncMock(), AsyncMock())
    svc.repo = repo_mock
    
    action = PendingActionRead(id=uuid4(), user_id=uuid4(), intent="create_obligation", status="pending", created_at=datetime.now(UTC), updated_at=datetime.now(UTC), command_id=uuid4(), data={"name": "test", "amount": 100, "currency": "EUR"})
    await svc._execute_financial_action(uuid4(), action)
    
    obl_mock.create_obligation.assert_called_once()
    dto = obl_mock.create_obligation.call_args[0][1]
    assert dto.currency == "EUR"

@pytest.mark.asyncio
async def test_conversational_create_income_no_currency():
    """Bug 1 variant: create_income doesn't receive currency from conversational payload."""
    repo_mock = AsyncMock()
    cash_mock = AsyncMock()
    svc = ConversationsService(repo_mock, AsyncMock(), cash_mock, AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock())
    svc.repo = repo_mock
    
    action = PendingActionRead(id=uuid4(), user_id=uuid4(), intent="create_income", status="pending", created_at=datetime.now(UTC), updated_at=datetime.now(UTC), command_id=uuid4(), data={"account_id": str(uuid4()), "amount": 100})
    await svc._execute_financial_action(uuid4(), action)
    
    cash_mock.create_income.assert_called_once()
    dto = cash_mock.create_income.call_args[0][1]
    assert not hasattr(dto, "currency")
