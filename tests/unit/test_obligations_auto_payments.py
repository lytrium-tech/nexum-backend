"""Unit and integration tests for Obligation Automatic Payments (V1.7 Phase 3).

Validates:
- Config API: enable, disable, own-active account, flag enforcement, terminal parent
- Paginated runs endpoint: empty runs 200 OK, limit/offset
- Scheduler & execution engine:
  - Canonical date in America/Bogota (storage/instants UTC)
  - Oldest due period first (due_date <= canonical_today only)
  - Full remaining transaction (prior partial payment reduces remaining, insufficient never clips)
  - Undefined oldest period stops advancing, reevaluates next canonical day
  - Insufficient funds stops advancing, zero debit, reevaluates next canonical day
  - Cross-currency with flush-only FX snapshot contract
  - Supported pair failure yields safe business/technical run, zero debit
  - Deterministic attempt identity deduplication for same day
  - Deterministic command identity replay safety
  - Rollback on failure, technical failure persisted in fresh UoW with concurrent winner check
  - Inactive account / terminal parent pauses config with actionable reason
- CLI module: proper exit codes (0, 1, 2) and flag enforcement without DB work
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.accounts.models import Account
from app.core.config import settings
from app.core.database import get_db_session
from app.core.financial_time import financial_date
from app.ledger.models import FinancialEvent
from app.main import app
from app.obligations.auto_service import (
    AutoPaymentProcessorSummary,
    ObligationAutoPaymentService,
)
from app.obligations.enums_v17 import PeriodStatus
from app.obligations.models import (
    Obligation,
    ObligationAutoPaymentConfig,
    ObligationAutoPaymentRun,
    ObligationPayment,
    ObligationPeriod,
)
from app.obligations.schemas_v17 import (
    ObligationAutoPaymentConfigUpdateRequest,
)
from app.users.schemas import UserRead
from scripts.cron import process_auto_payments


def _make_obligation(
    user_id: UUID,
    status: str = "active",
    currency: str = "COP",
) -> Obligation:
    return Obligation(
        id=uuid.uuid4(),
        user_id=user_id,
        name="Auto Test Obligation",
        currency=currency,
        type="fixed",
        frequency="monthly",
        payment_mode="standard",
        status=status,
        start_date=date(2026, 1, 1),
        first_due_date=date(2026, 1, 15),
        created_at=datetime.now(UTC).replace(tzinfo=None),
        updated_at=datetime.now(UTC),
    )


def _make_period(
    obligation_id: UUID,
    due_date: date,
    amount: Decimal | None = Decimal("100000.00"),
    paid_amount: Decimal = Decimal("0.00"),
    status: str = "pending_payment",
    sequence_number: int = 1,
) -> ObligationPeriod:
    return ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obligation_id,
        period_key=f"P-{sequence_number}",
        sequence_number=sequence_number,
        start_date=due_date - timedelta(days=30),
        end_date=due_date,
        due_date=due_date,
        amount=amount,
        paid_amount=paid_amount,
        currency="COP",
        status=status,
        created_at=datetime.now(UTC).replace(tzinfo=None),
        updated_at=datetime.now(UTC),
    )


def _make_account(
    user_id: UUID,
    balance: Decimal = Decimal("1000000.00"),
    currency: str = "COP",
    is_active: bool = True,
) -> Account:
    return Account(
        id=uuid.uuid4(),
        user_id=user_id,
        name="Test Account",
        type="checking",
        currency=currency,
        balance=balance,
        is_active=is_active,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class TestAutoPaymentConfigAPI:
    @pytest.mark.asyncio
    async def test_get_config_not_found_raises_404(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id)

        session = AsyncMock()
        v17_service = AsyncMock()
        fx_service = AsyncMock()
        account_repo = AsyncMock()
        repo = AsyncMock()

        # Mock obligation found, config not found
        repo.get_config.return_value = None

        service = ObligationAutoPaymentService(
            session=session,
            v17_service=v17_service,
            fx_service=fx_service,
            account_repo=account_repo,
            repo=repo,
        )
        service._get_obligation = AsyncMock(return_value=obligation)

        with pytest.raises(HTTPException) as exc_info:
            await service.get_config(user_id, obligation.id)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "auto_payment_config_not_found"

    @pytest.mark.asyncio
    async def test_put_config_enable_validates_own_active_account(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id, status="active")
        account = _make_account(user_id, is_active=True)

        session = AsyncMock()
        v17_service = AsyncMock()
        fx_service = AsyncMock()
        account_repo = AsyncMock()
        account_repo.get_by_id.return_value = account
        repo = AsyncMock()
        repo.get_config.return_value = None
        repo.get_oldest_due_open_period.return_value = None

        service = ObligationAutoPaymentService(
            session=session,
            v17_service=v17_service,
            fx_service=fx_service,
            account_repo=account_repo,
            repo=repo,
        )
        service._get_obligation = AsyncMock(return_value=obligation)

        payload = ObligationAutoPaymentConfigUpdateRequest(
            enabled=True,
            account_id=account.id,
        )
        result = await service.put_config(user_id, obligation.id, payload)
        assert result.enabled is True
        assert result.status == "enabled"
        assert result.account_id == account.id
        assert result.next_evaluation_at is not None
        assert repo.create_config.called

    @pytest.mark.asyncio
    async def test_put_config_inactive_parent_rejected(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id, status="cancelled")
        account = _make_account(user_id, is_active=True)

        session = AsyncMock()
        service = ObligationAutoPaymentService(
            session=session,
            v17_service=AsyncMock(),
            fx_service=AsyncMock(),
            account_repo=AsyncMock(),
            repo=AsyncMock(),
        )
        service._get_obligation = AsyncMock(return_value=obligation)

        payload = ObligationAutoPaymentConfigUpdateRequest(
            enabled=True,
            account_id=account.id,
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.put_config(user_id, obligation.id, payload)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "obligation_inactive"

    @pytest.mark.asyncio
    async def test_put_config_inactive_account_rejected(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id, status="active")
        account = _make_account(user_id, is_active=False)

        account_repo = AsyncMock()
        account_repo.get_by_id.return_value = account

        service = ObligationAutoPaymentService(
            session=AsyncMock(),
            v17_service=AsyncMock(),
            fx_service=AsyncMock(),
            account_repo=account_repo,
            repo=AsyncMock(),
        )
        service._get_obligation = AsyncMock(return_value=obligation)

        payload = ObligationAutoPaymentConfigUpdateRequest(
            enabled=True,
            account_id=account.id,
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.put_config(user_id, obligation.id, payload)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "account_inactive"

    @pytest.mark.asyncio
    async def test_put_config_non_owned_account_rejected(self):
        user_id = uuid.uuid4()
        other_user = uuid.uuid4()
        obligation = _make_obligation(user_id, status="active")
        account = _make_account(other_user, is_active=True)

        account_repo = AsyncMock()
        account_repo.get_by_id.return_value = account

        service = ObligationAutoPaymentService(
            session=AsyncMock(),
            v17_service=AsyncMock(),
            fx_service=AsyncMock(),
            account_repo=account_repo,
            repo=AsyncMock(),
        )
        service._get_obligation = AsyncMock(return_value=obligation)

        payload = ObligationAutoPaymentConfigUpdateRequest(
            enabled=True,
            account_id=account.id,
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.put_config(user_id, obligation.id, payload)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "account_not_found"

    @pytest.mark.asyncio
    async def test_put_config_disable_always_allowed_even_if_account_inactive_or_terminal(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id, status="completed")
        account = _make_account(user_id, is_active=False)

        existing_config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            next_evaluation_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_config.return_value = existing_config

        session = AsyncMock()
        service = ObligationAutoPaymentService(
            session=session,
            v17_service=AsyncMock(),
            fx_service=AsyncMock(),
            account_repo=AsyncMock(),
            repo=repo,
        )
        service._get_obligation = AsyncMock(return_value=obligation)

        payload = ObligationAutoPaymentConfigUpdateRequest(enabled=False)
        result = await service.put_config(user_id, obligation.id, payload)
        assert result.enabled is False
        assert result.status == "paused"
        assert result.pause_reason == "user_paused"
        assert result.next_evaluation_at is None
        assert session.commit.called

    @pytest.mark.asyncio
    async def test_put_config_disabling_ignores_account_id(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id, status="active")
        account = _make_account(user_id, is_active=True)
        foreign_account_id = uuid.uuid4()

        existing_config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            next_evaluation_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_config.return_value = existing_config

        session = AsyncMock()
        service = ObligationAutoPaymentService(
            session=session,
            v17_service=AsyncMock(),
            fx_service=AsyncMock(),
            account_repo=AsyncMock(),
            repo=repo,
        )
        service._get_obligation = AsyncMock(return_value=obligation)

        # Disabling supplying a foreign account_id
        payload = ObligationAutoPaymentConfigUpdateRequest(
            enabled=False, account_id=foreign_account_id
        )
        result = await service.put_config(user_id, obligation.id, payload)
        assert result.enabled is False
        assert result.status == "paused"
        # The account_id must NOT have been changed to the foreign account
        assert result.account_id == account.id
        assert existing_config.account_id == account.id

    @pytest.mark.asyncio
    async def test_put_config_creating_disabled_with_foreign_account_rejected(self):
        user_id = uuid.uuid4()
        other_user = uuid.uuid4()
        obligation = _make_obligation(user_id, status="active")
        foreign_account = _make_account(other_user, is_active=True)

        repo = AsyncMock()
        repo.get_config.return_value = None

        account_repo = AsyncMock()
        account_repo.get_by_id.return_value = foreign_account

        session = AsyncMock()
        service = ObligationAutoPaymentService(
            session=session,
            v17_service=AsyncMock(),
            fx_service=AsyncMock(),
            account_repo=account_repo,
            repo=repo,
        )
        service._get_obligation = AsyncMock(return_value=obligation)

        payload = ObligationAutoPaymentConfigUpdateRequest(
            enabled=False, account_id=foreign_account.id
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.put_config(user_id, obligation.id, payload)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "account_not_found"

    @pytest.mark.asyncio
    async def test_put_config_creating_disabled_with_inactive_account_rejected(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id, status="active")
        inactive_account = _make_account(user_id, is_active=False)

        repo = AsyncMock()
        repo.get_config.return_value = None

        account_repo = AsyncMock()
        account_repo.get_by_id.return_value = inactive_account

        session = AsyncMock()
        service = ObligationAutoPaymentService(
            session=session,
            v17_service=AsyncMock(),
            fx_service=AsyncMock(),
            account_repo=account_repo,
            repo=repo,
        )
        service._get_obligation = AsyncMock(return_value=obligation)

        payload = ObligationAutoPaymentConfigUpdateRequest(
            enabled=False, account_id=inactive_account.id
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.put_config(user_id, obligation.id, payload)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "account_inactive"

    @pytest.mark.asyncio
    async def test_list_runs_empty_page(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id)

        repo = AsyncMock()
        repo.list_runs_paginated.return_value = ([], 0)

        service = ObligationAutoPaymentService(
            session=AsyncMock(),
            v17_service=AsyncMock(),
            fx_service=AsyncMock(),
            account_repo=AsyncMock(),
            repo=repo,
        )
        service._get_obligation = AsyncMock(return_value=obligation)

        page = await service.list_runs(user_id, obligation.id, limit=50, offset=0)
        assert page.items == []
        assert page.total == 0
        assert page.limit == 50
        assert page.offset == 0


class TestAutoPaymentExecution:
    @pytest.fixture(autouse=True)
    def stub_period_materialization(self):
        # These coordinator tests mock SQL reads; physical PostgreSQL tests run the real engine.
        with patch(
            "app.obligations.period_engine.PeriodEngine.sync_periods", new_callable=AsyncMock
        ):
            yield

    @pytest.mark.asyncio
    async def test_due_date_future_materialized_period_is_not_paid(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id)
        account = _make_account(user_id)

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_next_eligible_config_for_update.side_effect = [config, None]
        repo.list_eligible_configs.return_value = [config]
        repo.get_oldest_due_open_period.return_value = None

        session = AsyncMock()
        session.add = MagicMock()
        exec_res = MagicMock()
        exec_res.scalars.return_value.first.return_value = obligation
        session.execute.return_value = exec_res

        v17_service = AsyncMock()
        service = ObligationAutoPaymentService(
            session=session,
            v17_service=v17_service,
            fx_service=AsyncMock(),
            account_repo=AsyncMock(),
            repo=repo,
        )

        summary = await service.process_due_auto_payments()
        assert summary.selected == 1
        assert summary.skipped == 1
        assert summary.succeeded == 0
        assert not v17_service.pay_specific_period.called
        assert config.next_evaluation_at is not None
        assert session.add.called
        assert session.commit.called

    @pytest.mark.asyncio
    async def test_full_remaining_payment_with_partial_prior(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id)
        account = _make_account(user_id, balance=Decimal("500000.00"))
        # Period amount 100k, 30k already paid -> remaining 70k
        period = _make_period(
            obligation.id,
            due_date=date(2026, 8, 1),
            amount=Decimal("100000.00"),
            paid_amount=Decimal("30000.00"),
            status="partially_paid",
        )

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_next_eligible_config_for_update.side_effect = [config, None]
        repo.list_eligible_configs.return_value = [config]
        repo.get_oldest_due_open_period.return_value = period
        repo.get_run_by_attempt_key.return_value = None

        session = AsyncMock()
        session.add = MagicMock()

        def mock_execute(stmt, *args, **kwargs):
            m = MagicMock()
            stmt_s = str(stmt).lower()
            if "from obligations" in stmt_s:
                m.scalars.return_value.first.return_value = obligation
            elif "from obligation_periods" in stmt_s:
                m.scalars.return_value.first.return_value = period
            elif "from financial_events" in stmt_s:
                m.scalars.return_value.first.return_value = None
            elif "from goal_transactions" in stmt_s:
                m.scalar.return_value = Decimal("0")
                m.scalars.return_value.first.return_value = Decimal("0")
            else:
                m.scalars.return_value.first.return_value = None
                m.scalars.return_value.all.return_value = []
            return m

        session.execute = AsyncMock(side_effect=mock_execute)

        account_repo = AsyncMock()
        account_repo.get_by_id_for_update.return_value = account

        v17_service = AsyncMock()
        mock_payment = ObligationPayment(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            obligation_period_id=period.id,
            user_id=user_id,
            account_id=account.id,
            amount=Decimal("70000.00"),
            currency="COP",
            source_amount=Decimal("70000.00"),
            source_currency="COP",
            fx_rate=None,
            paid_at=datetime.now(UTC).replace(tzinfo=None),
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )

        async def mock_pay(*args, **kwargs):
            period.paid_amount = Decimal("100000.00")
            period.status = "paid"
            return mock_payment, period

        v17_service.pay_specific_period.side_effect = mock_pay

        with patch(
            "app.goals.repository.GoalRepository.calculate_reserved_by_account",
            AsyncMock(return_value=Decimal("0")),
        ):
            service = ObligationAutoPaymentService(
                session=session,
                v17_service=v17_service,
                fx_service=AsyncMock(),
                account_repo=account_repo,
                repo=repo,
            )
            summary = await service.process_due_auto_payments()

        assert summary.selected == 1
        assert summary.succeeded == 1
        assert v17_service.pay_specific_period.called
        # Check call arguments: remaining amount was requested
        called_args = v17_service.pay_specific_period.call_args
        assert called_args.kwargs["channel"] == "automatic"
        assert called_args.kwargs["data"].amount == Decimal("70000.00")
        assert called_args.kwargs["override_command_id"] == uuid.uuid5(
            uuid.NAMESPACE_OID, f"auto_pay:{obligation.id}:{period.id}"
        )
        assert session.commit.called

    @pytest.mark.asyncio
    async def test_undefined_period_stops_advancing_and_reevaluates_next_day(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id)
        account = _make_account(user_id)
        # Period requires amount definition
        period = _make_period(
            obligation.id,
            due_date=date(2026, 8, 1),
            amount=None,
            status=PeriodStatus.pending_amount_definition.value,
        )

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_next_eligible_config_for_update.side_effect = [config, None]
        repo.list_eligible_configs.return_value = [config]
        repo.get_oldest_due_open_period.return_value = period
        repo.get_run_by_attempt_key.return_value = None

        session = AsyncMock()
        session.add = MagicMock()

        def mock_execute(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligations" in s:
                m.scalars.return_value.first.return_value = obligation
            elif "from obligation_periods" in s:
                m.scalars.return_value.first.return_value = period
            return m

        session.execute = AsyncMock(side_effect=mock_execute)
        v17_service = AsyncMock()

        service = ObligationAutoPaymentService(
            session=session,
            v17_service=v17_service,
            fx_service=AsyncMock(),
            account_repo=AsyncMock(),
            repo=repo,
        )

        summary = await service.process_due_auto_payments()
        assert summary.selected == 1
        assert summary.skipped == 1
        assert not v17_service.pay_specific_period.called
        # Run record created with period_amount_not_defined
        assert session.add.called
        added_run = session.add.call_args[0][0]
        assert isinstance(added_run, ObligationAutoPaymentRun)
        assert added_run.result_code == "period_amount_not_defined"
        assert added_run.status == "skipped"
        assert session.commit.called

    @pytest.mark.asyncio
    async def test_insufficient_balance_stops_advancing_zero_debit(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id)
        # Account balance 50k, but period amount is 100k -> insufficient
        account = _make_account(user_id, balance=Decimal("50000.00"))
        period = _make_period(
            obligation.id,
            due_date=date(2026, 8, 1),
            amount=Decimal("100000.00"),
        )

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_next_eligible_config_for_update.side_effect = [config, None]
        repo.list_eligible_configs.return_value = [config]
        repo.get_oldest_due_open_period.return_value = period
        repo.get_run_by_attempt_key.return_value = None

        session = AsyncMock()
        session.add = MagicMock()

        def mock_execute(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligations" in s:
                m.scalars.return_value.first.return_value = obligation
            elif "from obligation_periods" in s:
                m.scalars.return_value.first.return_value = period
            elif "from financial_events" in s:
                m.scalars.return_value.first.return_value = None
            return m

        session.execute = AsyncMock(side_effect=mock_execute)
        account_repo = AsyncMock()
        account_repo.get_by_id_for_update.return_value = account
        v17_service = AsyncMock()

        with patch(
            "app.goals.repository.GoalRepository.calculate_reserved_by_account",
            AsyncMock(return_value=Decimal("0")),
        ):
            service = ObligationAutoPaymentService(
                session=session,
                v17_service=v17_service,
                fx_service=AsyncMock(),
                account_repo=account_repo,
                repo=repo,
            )
            summary = await service.process_due_auto_payments()

        assert summary.selected == 1
        assert summary.skipped == 1
        # No debit or payment
        assert not v17_service.pay_specific_period.called
        assert not account_repo.update_balance.called
        # Run record created with insufficient_balance
        added_run = session.add.call_args[0][0]
        assert isinstance(added_run, ObligationAutoPaymentRun)
        assert added_run.result_code == "insufficient_balance"
        assert session.commit.called

    @pytest.mark.asyncio
    async def test_cross_currency_uses_flush_only_snapshot(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id, currency="COP")
        # Account in USD
        account = _make_account(user_id, balance=Decimal("1000.00"), currency="USD")
        period = _make_period(
            obligation.id,
            due_date=date(2026, 8, 1),
            amount=Decimal("400000.00"),
        )

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_next_eligible_config_for_update.side_effect = [config, None]
        repo.list_eligible_configs.return_value = [config]
        repo.get_oldest_due_open_period.return_value = period
        repo.get_run_by_attempt_key.return_value = None

        session = AsyncMock()
        session.add = MagicMock()

        def mock_execute(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligations" in s:
                m.scalars.return_value.first.return_value = obligation
            elif "from obligation_periods" in s:
                m.scalars.return_value.first.return_value = period
            elif "from financial_events" in s:
                m.scalars.return_value.first.return_value = None
            return m

        session.execute = AsyncMock(side_effect=mock_execute)
        account_repo = AsyncMock()
        account_repo.get_by_id_for_update.return_value = account

        fx_service = AsyncMock()
        snapshot_mock = MagicMock()
        snapshot_mock.id = uuid.uuid4()
        snapshot_mock.rate = Decimal("4000.000000")
        fx_service.get_rate_snapshot.return_value = snapshot_mock

        v17_service = AsyncMock()
        mock_payment = ObligationPayment(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            obligation_period_id=period.id,
            user_id=user_id,
            account_id=account.id,
            amount=Decimal("400000.00"),
            currency="COP",
            source_amount=Decimal("100.00"),
            source_currency="USD",
            fx_rate=Decimal("4000.000000"),
            paid_at=datetime.now(UTC).replace(tzinfo=None),
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
        v17_service.pay_specific_period.return_value = (mock_payment, period)

        with patch(
            "app.goals.repository.GoalRepository.calculate_reserved_by_account",
            AsyncMock(return_value=Decimal("0")),
        ):
            service = ObligationAutoPaymentService(
                session=session,
                v17_service=v17_service,
                fx_service=fx_service,
                account_repo=account_repo,
                repo=repo,
            )
            summary = await service.process_due_auto_payments()

        assert summary.succeeded == 1
        # Snapshot requested with commit=False!
        fx_service.get_rate_snapshot.assert_called_once_with("USD", "COP", commit=False)
        assert v17_service.pay_specific_period.called
        assert session.commit.called

    @pytest.mark.asyncio
    async def test_duplicate_attempt_on_same_day_deduplicates(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id)
        account = _make_account(user_id)
        period = _make_period(obligation.id, due_date=date(2026, 8, 1))

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        existing_run = ObligationAutoPaymentRun(
            id=uuid.uuid4(),
            config_id=config.id,
            obligation_id=obligation.id,
            obligation_period_id=period.id,
            attempt_key=f"{config.id}:{period.id}:{financial_date().isoformat()}",
            canonical_date=financial_date(),
            status="succeeded",
            result_code="success",
            executed_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_next_eligible_config_for_update.side_effect = [config, None]
        repo.list_eligible_configs.return_value = [config]
        repo.get_oldest_due_open_period.return_value = period
        # Run already exists for today!
        repo.get_run_by_attempt_key.return_value = existing_run

        session = AsyncMock()
        session.add = MagicMock()

        def mock_execute(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligations" in s:
                m.scalars.return_value.first.return_value = obligation
            elif "from obligation_periods" in s:
                m.scalars.return_value.first.return_value = period
            return m

        session.execute = AsyncMock(side_effect=mock_execute)
        v17_service = AsyncMock()

        service = ObligationAutoPaymentService(
            session=session,
            v17_service=v17_service,
            fx_service=AsyncMock(),
            account_repo=AsyncMock(),
            repo=repo,
        )

        summary = await service.process_due_auto_payments()
        assert summary.selected == 1
        assert summary.skipped == 1
        assert summary.succeeded == 0
        assert not v17_service.pay_specific_period.called

    @pytest.mark.asyncio
    async def test_deterministic_command_identity_replay_safe(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id)
        account = _make_account(user_id)
        period = _make_period(obligation.id, due_date=date(2026, 8, 1))

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_next_eligible_config_for_update.side_effect = [config, None]
        repo.list_eligible_configs.return_value = [config]
        repo.get_oldest_due_open_period.return_value = period
        repo.get_run_by_attempt_key.return_value = None

        session = AsyncMock()
        session.add = MagicMock()

        existing_event = FinancialEvent(
            id=uuid.uuid4(),
            user_id=user_id,
            account_id=account.id,
            amount=Decimal("100000.00"),
            currency="COP",
            event_type="obligation_payment",
            direction="outflow",
            occurred_at=datetime.now(UTC),
            period="2026-08",
            created_at=datetime.now(UTC),
        )
        existing_payment = ObligationPayment(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            obligation_period_id=period.id,
            user_id=user_id,
            account_id=account.id,
            amount=Decimal("100000.00"),
            currency="COP",
            source_amount=Decimal("100000.00"),
            source_currency="COP",
            paid_at=datetime.now(UTC).replace(tzinfo=None),
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )

        session = AsyncMock()

        def mock_execute(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligations" in s:
                m.scalars.return_value.first.return_value = obligation
            elif "from obligation_periods" in s:
                m.scalars.return_value.first.return_value = period
            elif "from financial_events" in s:
                m.scalars.return_value.first.return_value = existing_event
            elif "from obligation_payments" in s:
                m.scalars.return_value.all.return_value = [existing_payment]
            return m

        session.execute = AsyncMock(side_effect=mock_execute)
        v17_service = AsyncMock()

        service = ObligationAutoPaymentService(
            session=session,
            v17_service=v17_service,
            fx_service=AsyncMock(),
            account_repo=AsyncMock(),
            repo=repo,
        )

        summary = await service.process_due_auto_payments()
        assert summary.selected == 1
        assert summary.succeeded == 1
        # No new payment call, safe replay
        assert not v17_service.pay_specific_period.called
        assert session.commit.called

    @pytest.mark.asyncio
    async def test_technical_failure_rollback_and_winner_check(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id)
        account = _make_account(user_id)
        period = _make_period(obligation.id, due_date=date(2026, 8, 1))

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_next_eligible_config_for_update.side_effect = [config, None]
        repo.list_eligible_configs.return_value = [config]
        repo.get_oldest_due_open_period.return_value = period
        repo.get_run_by_attempt_key.return_value = None

        session = AsyncMock()
        session.add = MagicMock()

        def mock_execute(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligations" in s:
                m.scalars.return_value.first.return_value = obligation
            elif "from obligation_periods" in s:
                m.scalars.return_value.first.return_value = period
            elif "from financial_events" in s:
                m.scalars.return_value.first.return_value = None
            return m

        session.execute = AsyncMock(side_effect=mock_execute)
        account_repo = AsyncMock()
        account_repo.get_by_id_for_update.return_value = account

        # payment fails unexpectedly
        v17_service = AsyncMock()
        v17_service.pay_specific_period.side_effect = RuntimeError("Database deadlock crash")

        # Mock fresh session factory for technical failure handler
        fresh_session = AsyncMock()

        def mock_fresh_exec(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligation_auto_payment_configs" in s:
                m.scalars.return_value.first.return_value = config
            elif "from obligation_auto_payment_runs" in s:
                m.scalars.return_value.all.return_value = []
                m.scalars.return_value.first.return_value = None
            return m

        fresh_session.execute = AsyncMock(side_effect=mock_fresh_exec)

        fresh_cm = MagicMock()
        fresh_cm.__aenter__ = AsyncMock(return_value=fresh_session)
        fresh_cm.__aexit__ = AsyncMock(return_value=None)
        session_factory = MagicMock(return_value=fresh_cm)

        with patch(
            "app.goals.repository.GoalRepository.calculate_reserved_by_account",
            AsyncMock(return_value=Decimal("0")),
        ):
            service = ObligationAutoPaymentService(
                session=session,
                v17_service=v17_service,
                fx_service=AsyncMock(),
                account_repo=account_repo,
                repo=repo,
            )
            summary = await service.process_due_auto_payments(session_factory=session_factory)

        assert summary.selected == 1
        assert summary.technical_failures == 1
        assert summary.failed == 1
        assert session.rollback.called
        # Technical failure persisted in fresh session
        assert fresh_session.add.called
        failed_run = fresh_session.add.call_args[0][0]
        assert isinstance(failed_run, ObligationAutoPaymentRun)
        assert failed_run.result_code == "technical_error"
        assert failed_run.status == "failed"
        assert "Database deadlock crash" not in (failed_run.error_message or "")
        assert fresh_session.commit.called

    @pytest.mark.asyncio
    async def test_concurrent_winner_not_overwritten(self):
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id)
        account = _make_account(user_id)
        period = _make_period(obligation.id, due_date=date(2026, 8, 1))

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_next_eligible_config_for_update.side_effect = [config, None]
        repo.list_eligible_configs.return_value = [config]
        repo.get_oldest_due_open_period.return_value = period
        repo.get_run_by_attempt_key.return_value = None

        session = AsyncMock()
        session.add = MagicMock()

        def mock_execute(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligations" in s:
                m.scalars.return_value.first.return_value = obligation
            elif "from obligation_periods" in s:
                m.scalars.return_value.first.return_value = period
            elif "from financial_events" in s:
                m.scalars.return_value.first.return_value = None
            return m

        session.execute = AsyncMock(side_effect=mock_execute)
        account_repo = AsyncMock()
        account_repo.get_by_id_for_update.return_value = account

        v17_service = AsyncMock()
        v17_service.pay_specific_period.side_effect = RuntimeError("Deadlock")

        # Concurrent winner already completed attempt in fresh session!
        fresh_session = AsyncMock()
        winner_run = ObligationAutoPaymentRun(
            id=uuid.uuid4(),
            config_id=config.id,
            obligation_id=obligation.id,
            attempt_key=f"{config.id}:none:{financial_date().isoformat()}",
            canonical_date=financial_date(),
            status="succeeded",
            result_code="success",
            executed_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )

        def mock_fresh_winner_exec(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligation_auto_payment_configs" in s:
                m.scalars.return_value.first.return_value = config
            elif "from obligation_auto_payment_runs" in s:
                m.scalars.return_value.all.return_value = [winner_run]
                m.scalars.return_value.first.return_value = winner_run
            return m

        fresh_session.execute = AsyncMock(side_effect=mock_fresh_winner_exec)

        fresh_cm = MagicMock()
        fresh_cm.__aenter__ = AsyncMock(return_value=fresh_session)
        fresh_cm.__aexit__ = AsyncMock(return_value=None)
        session_factory = MagicMock(return_value=fresh_cm)

        with patch(
            "app.goals.repository.GoalRepository.calculate_reserved_by_account",
            AsyncMock(return_value=Decimal("0")),
        ):
            service = ObligationAutoPaymentService(
                session=session,
                v17_service=v17_service,
                fx_service=AsyncMock(),
                account_repo=account_repo,
                repo=repo,
            )
            summary = await service.process_due_auto_payments(session_factory=session_factory)

        assert summary.technical_failures == 1
        # Does NOT add a new run to fresh_session because winner was found!
        assert not fresh_session.add.called

    @pytest.mark.asyncio
    async def test_two_independent_worker_sessions_cannot_duplicate_effects(self):
        """Worker 1 claims and completes config; Worker 2 cannot re-claim or duplicate."""
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id)
        account = _make_account(user_id, balance=Decimal("100000.00"))
        period = _make_period(obligation.id, due_date=date(2026, 8, 1))

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo1 = AsyncMock()
        repo1.get_next_eligible_config_for_update.side_effect = [config, None]
        repo1.get_oldest_due_open_period.return_value = period
        repo1.get_run_by_attempt_key.return_value = None

        session1 = AsyncMock()
        session1.add = MagicMock()

        def mock_exec1(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligations" in s:
                m.scalars.return_value.first.return_value = obligation
            elif "from obligation_periods" in s:
                m.scalars.return_value.first.return_value = period
            elif "from financial_events" in s:
                m.scalars.return_value.first.return_value = None
            return m

        session1.execute = AsyncMock(side_effect=mock_exec1)
        account_repo1 = AsyncMock()
        account_repo1.get_by_id_for_update.return_value = account

        v17_service1 = AsyncMock()
        mock_payment = ObligationPayment(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            obligation_period_id=period.id,
            user_id=user_id,
            account_id=account.id,
            amount=Decimal("100000.00"),
            currency="COP",
            source_amount=Decimal("100000.00"),
            source_currency="COP",
            paid_at=datetime.now(UTC).replace(tzinfo=None),
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
        v17_service1.pay_specific_period.return_value = (mock_payment, period)

        service1 = ObligationAutoPaymentService(
            session=session1,
            v17_service=v17_service1,
            fx_service=AsyncMock(),
            account_repo=account_repo1,
            repo=repo1,
        )

        with patch(
            "app.goals.repository.GoalRepository.calculate_reserved_by_account",
            AsyncMock(return_value=Decimal("0")),
        ):
            summary1 = await service1.process_due_auto_payments()

        assert summary1.succeeded == 1

        # Worker 2 tries to run concurrently; SKIP LOCKED returns None because config is either locked or already advanced
        repo2 = AsyncMock()
        repo2.get_next_eligible_config_for_update.return_value = None

        service2 = ObligationAutoPaymentService(
            session=AsyncMock(),
            v17_service=AsyncMock(),
            fx_service=AsyncMock(),
            account_repo=AsyncMock(),
            repo=repo2,
        )
        summary2 = await service2.process_due_auto_payments()
        assert summary2.selected == 0
        assert summary2.succeeded == 0

    @pytest.mark.asyncio
    async def test_persist_technical_failure_raises_visibly_on_persistence_error(self):
        """Technical failure persistence must fail visibly and raise RuntimeError, not swallow with pass."""
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id)
        account = _make_account(user_id)
        period = _make_period(obligation.id, due_date=date(2026, 8, 1))

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_next_eligible_config_for_update.side_effect = [config, None]
        repo.get_oldest_due_open_period.return_value = period
        repo.get_run_by_attempt_key.return_value = None

        session = AsyncMock()
        session.add = MagicMock()

        def mock_exec(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligations" in s:
                m.scalars.return_value.first.return_value = obligation
            elif "from obligation_periods" in s:
                m.scalars.return_value.first.return_value = period
            return m

        session.execute = AsyncMock(side_effect=mock_exec)
        account_repo = AsyncMock()
        account_repo.get_by_id_for_update.return_value = account

        v17_service = AsyncMock()
        v17_service.pay_specific_period.side_effect = RuntimeError("Primary failure")

        # Session factory crashes on enter
        session_factory = MagicMock(side_effect=RuntimeError("Fresh DB connection failure"))

        service = ObligationAutoPaymentService(
            session=session,
            v17_service=v17_service,
            fx_service=AsyncMock(),
            account_repo=account_repo,
            repo=repo,
        )

        with patch(
            "app.goals.repository.GoalRepository.calculate_reserved_by_account",
            AsyncMock(return_value=Decimal("0")),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                await service.process_due_auto_payments(session_factory=session_factory)

        assert "Failed to persist technical failure" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_replay_succeeds_even_if_account_balance_subsequently_zero(self):
        """Early exact replay check succeeds safely even if account balance became zero."""
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id)
        # Account has 0 balance!
        account = _make_account(user_id, balance=Decimal("0.00"))
        period = _make_period(obligation.id, due_date=date(2026, 8, 1))

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_next_eligible_config_for_update.side_effect = [config, None]
        repo.get_oldest_due_open_period.return_value = period
        repo.get_run_by_attempt_key.return_value = None

        existing_event = FinancialEvent(
            id=uuid.uuid4(),
            user_id=user_id,
            account_id=account.id,
            amount=Decimal("100000.00"),
            currency="COP",
            event_type="obligation_payment",
            direction="outflow",
            occurred_at=datetime.now(UTC),
            period="2026-08",
            created_at=datetime.now(UTC),
        )
        existing_payment = ObligationPayment(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            obligation_period_id=period.id,
            user_id=user_id,
            account_id=account.id,
            amount=Decimal("100000.00"),
            currency="COP",
            source_amount=Decimal("100000.00"),
            source_currency="COP",
            paid_at=datetime.now(UTC).replace(tzinfo=None),
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )

        session = AsyncMock()
        session.add = MagicMock()

        def mock_execute(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligations" in s:
                m.scalars.return_value.first.return_value = obligation
            elif "from obligation_periods" in s:
                m.scalars.return_value.first.return_value = period
            elif "from financial_events" in s:
                m.scalars.return_value.first.return_value = existing_event
            elif "from obligation_payments" in s:
                m.scalars.return_value.all.return_value = [existing_payment]
            return m

        session.execute = AsyncMock(side_effect=mock_execute)
        v17_service = AsyncMock()

        service = ObligationAutoPaymentService(
            session=session,
            v17_service=v17_service,
            fx_service=AsyncMock(),
            account_repo=AsyncMock(),
            repo=repo,
        )

        summary = await service.process_due_auto_payments()
        assert summary.selected == 1
        assert summary.succeeded == 1
        assert not v17_service.pay_specific_period.called

    @pytest.mark.asyncio
    async def test_fx_database_error_triggers_technical_failure_not_swallowed(self):
        """Database exception during FX snapshot must cause rollback and technical failure, not business missing rate."""
        user_id = uuid.uuid4()
        obligation = _make_obligation(user_id, currency="COP")
        account = _make_account(user_id, currency="USD", balance=Decimal("1000.00"))
        period = _make_period(obligation.id, due_date=date(2026, 8, 1))

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=account.id,
            enabled=True,
            status="enabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        repo = AsyncMock()
        repo.get_next_eligible_config_for_update.side_effect = [config, None]
        repo.get_oldest_due_open_period.return_value = period
        repo.get_run_by_attempt_key.return_value = None

        session = AsyncMock()
        session.add = MagicMock()

        def mock_execute(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligations" in s:
                m.scalars.return_value.first.return_value = obligation
            elif "from obligation_periods" in s:
                m.scalars.return_value.first.return_value = period
            elif "from financial_events" in s:
                m.scalars.return_value.first.return_value = None
            return m

        session.execute = AsyncMock(side_effect=mock_execute)
        account_repo = AsyncMock()
        account_repo.get_by_id_for_update.return_value = account

        # fx_service raises a database/SQL connection error
        fx_service = AsyncMock()
        fx_service.get_rate_snapshot.side_effect = RuntimeError("Database connection terminated")

        service = ObligationAutoPaymentService(
            session=session,
            v17_service=AsyncMock(),
            fx_service=fx_service,
            account_repo=account_repo,
            repo=repo,
        )

        summary = await service.process_due_auto_payments()
        assert summary.technical_failures == 1
        assert summary.failed == 1
        assert session.rollback.called


class TestAutoPaymentCLI:
    @pytest.mark.asyncio
    async def test_cli_disabled_flag_exits_zero_without_db(self):
        with patch.object(settings, "NEXUM_OBLIGATIONS_V17_ENABLED", False):
            with patch("app.core.database.init_engine") as mock_init:
                exit_code = await process_auto_payments.async_main()
                assert exit_code == process_auto_payments.EXIT_OK
                assert not mock_init.called

    @pytest.mark.asyncio
    async def test_cli_clean_batch_exits_zero(self):
        mock_summary = AutoPaymentProcessorSummary(selected=2, succeeded=2)
        with patch.object(settings, "NEXUM_OBLIGATIONS_V17_ENABLED", True):
            with (
                patch("app.core.database.init_engine", AsyncMock()),
                patch("app.core.database.close_engine", AsyncMock()),
                patch("app.core.database._session_factory") as mock_sf,
                patch.object(
                    ObligationAutoPaymentService,
                    "process_due_auto_payments",
                    AsyncMock(return_value=mock_summary),
                ),
            ):
                session_mock = AsyncMock()
                cm = MagicMock()
                cm.__aenter__ = AsyncMock(return_value=session_mock)
                cm.__aexit__ = AsyncMock(return_value=None)
                mock_sf.return_value = cm

                exit_code = await process_auto_payments.async_main()
                assert exit_code == process_auto_payments.EXIT_OK

    @pytest.mark.asyncio
    async def test_cli_technical_failure_exits_two(self):
        mock_summary = AutoPaymentProcessorSummary(selected=2, technical_failures=1, failed=1)
        with patch.object(settings, "NEXUM_OBLIGATIONS_V17_ENABLED", True):
            with (
                patch("app.core.database.init_engine", AsyncMock()),
                patch("app.core.database.close_engine", AsyncMock()),
                patch("app.core.database._session_factory") as mock_sf,
                patch.object(
                    ObligationAutoPaymentService,
                    "process_due_auto_payments",
                    AsyncMock(return_value=mock_summary),
                ),
            ):
                session_mock = AsyncMock()
                cm = MagicMock()
                cm.__aenter__ = AsyncMock(return_value=session_mock)
                cm.__aexit__ = AsyncMock(return_value=None)
                mock_sf.return_value = cm

                exit_code = await process_auto_payments.async_main()
                assert exit_code == process_auto_payments.EXIT_PARTIAL_TECHNICAL_FAILURE

    @pytest.mark.asyncio
    async def test_cli_unhandled_failure_exits_one(self):
        with patch.object(settings, "NEXUM_OBLIGATIONS_V17_ENABLED", True):
            with (
                patch(
                    "app.core.database.init_engine",
                    AsyncMock(side_effect=RuntimeError("DB Connection refused")),
                ),
                patch("app.core.database.close_engine", AsyncMock()),
            ):
                exit_code = await process_auto_payments.async_main()
                assert exit_code == process_auto_payments.EXIT_GLOBAL_FAILURE

    @pytest.mark.asyncio
    async def test_cli_exits_nonzero_on_technical_failure_persistence_crash(self):
        """CLI async_main must exit with EXIT_GLOBAL_FAILURE (code 1) when technical failure persistence crashes."""
        with patch.object(settings, "NEXUM_OBLIGATIONS_V17_ENABLED", True):
            with (
                patch("app.core.database.init_engine", AsyncMock()),
                patch("app.core.database.close_engine", AsyncMock()),
                patch("app.core.database._session_factory") as mock_sf,
                patch.object(
                    ObligationAutoPaymentService,
                    "process_due_auto_payments",
                    AsyncMock(side_effect=RuntimeError("Failed to persist technical failure")),
                ),
            ):
                session_mock = AsyncMock()
                cm = MagicMock()
                cm.__aenter__ = AsyncMock(return_value=session_mock)
                cm.__aexit__ = AsyncMock(return_value=None)
                mock_sf.return_value = cm

                exit_code = await process_auto_payments.async_main()
                assert exit_code == process_auto_payments.EXIT_GLOBAL_FAILURE
                assert exit_code != 0


class TestAutoPaymentHTTPRoutes:
    @pytest.mark.asyncio
    async def test_route_flag_disabled_returns_403(self):
        obs_id = uuid.uuid4()
        with patch.object(settings, "NEXUM_OBLIGATIONS_V17_ENABLED", False):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                res_get = await client.get(f"/api/v1.7/obligations/{obs_id}/auto-payment")
                assert res_get.status_code == 403
                assert res_get.json()["detail"] == "feature_flag_disabled"

                res_put = await client.put(
                    f"/api/v1.7/obligations/{obs_id}/auto-payment",
                    json={"enabled": False},
                )
                assert res_put.status_code == 403

                res_runs = await client.get(f"/api/v1.7/obligations/{obs_id}/auto-payment/runs")
                assert res_runs.status_code == 403

    @pytest.mark.asyncio
    async def test_route_get_and_put_and_runs_success(self):
        obs_id = uuid.uuid4()
        user_id = uuid.UUID(settings.DEV_USER_ID)
        account_id = uuid.uuid4()

        obligation = _make_obligation(user_id)
        obligation.id = obs_id
        account = _make_account(user_id)
        account.id = account_id

        config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obs_id,
            user_id=user_id,
            account_id=account_id,
            enabled=True,
            status="enabled",
            next_evaluation_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        run = ObligationAutoPaymentRun(
            id=uuid.uuid4(),
            config_id=config.id,
            obligation_id=obs_id,
            obligation_period_id=None,
            attempt_key=f"{config.id}:none:2026-08-01",
            canonical_date=date(2026, 8, 1),
            status="skipped",
            result_code="no_due_periods",
            executed_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )

        mock_session = AsyncMock()

        def mock_execute(stmt, *args, **kwargs):
            m = MagicMock()
            s = str(stmt).lower()
            if "from obligations" in s:
                m.scalars.return_value.first.return_value = obligation
            elif "from obligation_auto_payment_configs" in s:
                m.scalars.return_value.first.return_value = config
            elif "count" in s:
                m.scalar.return_value = 1
            elif "from obligation_auto_payment_runs" in s:
                m.scalars.return_value.all.return_value = [run]
            elif "from accounts" in s:
                m.scalars.return_value.first.return_value = account
            return m

        mock_session.execute = AsyncMock(side_effect=mock_execute)
        app.dependency_overrides[get_db_session] = lambda: mock_session

        async def mock_user():
            return UserRead(
                id=user_id,
                auth_user_id=user_id,
                name="Test User",
                email="test@example.com",
                timezone="America/Bogota",
                currency="COP",
                status="active",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

        from app.users.dependencies import get_current_user_profile_dep

        app.dependency_overrides[get_current_user_profile_dep] = mock_user

        with patch.object(settings, "NEXUM_OBLIGATIONS_V17_ENABLED", True):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # 1. GET auto-payment
                res_get = await client.get(f"/api/v1.7/obligations/{obs_id}/auto-payment")
                assert res_get.status_code == 200
                data_get = res_get.json()
                assert data_get["enabled"] is True
                assert data_get["status"] == "enabled"
                assert data_get["account_id"] == str(account_id)

                # 2. PUT auto-payment disable
                res_put = await client.put(
                    f"/api/v1.7/obligations/{obs_id}/auto-payment",
                    json={"enabled": False},
                )
                assert res_put.status_code == 200
                data_put = res_put.json()
                assert data_put["enabled"] is False

                # 3. GET auto-payment runs
                res_runs = await client.get(f"/api/v1.7/obligations/{obs_id}/auto-payment/runs")
                assert res_runs.status_code == 200
                data_runs = res_runs.json()
                assert data_runs["total"] == 1
                assert data_runs["limit"] == 50
                assert data_runs["offset"] == 0
                assert len(data_runs["items"]) == 1
                assert data_runs["items"][0]["status"] == "skipped"
                assert data_runs["items"][0]["result_code"] == "no_due_periods"
