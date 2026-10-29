import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.accounts.exceptions import AccountForbiddenError
from app.cash.exceptions import InsufficientFundsError
from app.conversations.prompts import render_read_response
from app.conversations.schemas import ConversationalRequest, PendingActionRead
from app.conversations.service import ConversationsService
from app.credit.exceptions import (
    CreditAccountInactiveError,
    CreditActivationBoundaryError,
    CreditAllocationError,
    CreditBillingCycleClosedError,
    CreditCardForbiddenError,
    CreditCardInactiveError,
    CreditCardNotFoundError,
    CreditFXRateUnavailableError,
    CreditGoalReservationBreachError,
    CreditIdempotencyConflictError,
    CreditInstallmentCeilingError,
    CreditLifecycleInvalidityError,
    CreditLimitExceededError,
    CreditStatementImmutableError,
    CreditUnsupportedCurrencyError,
    InvalidPaymentAmountError,
)
from app.credit.schemas import (
    CreditCardAdjustmentRead,
    CreditCardBillingCycleRead,
    CreditCardPaymentAllocationRead,
    CreditCardPaymentCreate,
    CreditCardPaymentRead,
    CreditCardPurchaseCreate,
    CreditCardRead,
    CreditCardStatementComponentRead,
    CreditCardStatementRead,
    CreditCardStatusRead,
    CreditSummaryRead,
)
from app.intelligence.schemas import (
    IntelligenceCreditCardRead,
    IntelligenceDebtRead,
    IntelligenceSnapshotRead,
    SnapshotDebt,
    SnapshotTruth,
)
from app.intelligence.service import IntelligenceService

# ==============================================================================
# 1. Intelligence Credit Consumer Tests (Acceptance Criterion 1)
# ==============================================================================


@pytest.mark.asyncio
async def test_intelligence_get_snapshot_consumes_canonical_facts():
    """Validates that IntelligenceService.get_snapshot derives credit facts from

    CreditCardService.get_credit_summary and does NOT consume stale current_debt
    or fabricated minimum payments.
    """
    session = AsyncMock()
    service = IntelligenceService(session=session)
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()

    mock_status = CreditCardStatusRead(
        card_id=card_id,
        name="Visa Infinite",
        credit_limit=Decimal("10000000.00"),
        total_debt=Decimal("3000000.00"),
        billed_debt=Decimal("1000000.00"),
        unbilled_debt=Decimal("2000000.00"),
        available_credit=Decimal("7000000.00"),
        payment_required=Decimal("1000000.00"),
        minimum_payment=None,  # Nullable/unknown; no invented minimum payment
        next_payment_estimate=Decimal("1000000.00"),
        statement_balance=Decimal("1000000.00"),
        monthly_cc_payment=Decimal("1000000.00"),
        cutoff_day=15,
        payment_due_day=5,
        next_payment_due_date="2026-11-05",
        purchases_count=2,
        payments_count=1,
        data_quality={"minimum_payment": "unknown_unless_issuer_fact"},
    )
    mock_summary = CreditSummaryRead(
        total_credit_limit=Decimal("10000000.00"),
        total_debt=Decimal("3000000.00"),
        total_available_credit=Decimal("7000000.00"),
        total_monthly_cc_payment=Decimal("1000000.00"),
        total_payment_required=Decimal("1000000.00"),
        total_next_payment_estimate=Decimal("1000000.00"),
        cards=[mock_status],
    )

    with (
        patch("app.intelligence.service.CreditCardService") as mock_cc_service_class,
        patch.object(service.repo, "get_cash_metrics", new_callable=AsyncMock) as m_cash,
        patch.object(
            service.repo, "get_cash_metrics_by_currency", new_callable=AsyncMock
        ) as m_cash_curr,
        patch.object(service.repo, "get_cashflow_metrics", new_callable=AsyncMock) as m_cf,
        patch.object(
            service.repo, "get_cashflow_metrics_by_currency", new_callable=AsyncMock
        ) as m_cf_curr,
        patch.object(
            service.repo, "get_historical_cashflow_metrics", new_callable=AsyncMock
        ) as m_hcf,
        patch.object(service.repo, "get_goals_metrics", new_callable=AsyncMock) as m_goals,
        patch.object(
            service.repo, "get_obligations_metrics", new_callable=AsyncMock
        ) as m_obl_metrics,
        patch.object(service.repo, "get_transfers_metrics", new_callable=AsyncMock) as m_transfers,
        patch.object(service.repo, "get_recent_activity", new_callable=AsyncMock) as m_rec,
        patch("app.obligations.repository.ObligationRepository") as mock_obl_repo_class,
        patch("app.goals.repository.GoalRepository") as mock_goal_repo_class,
    ):
        mock_cc_service_class.return_value.get_credit_summary = AsyncMock(return_value=mock_summary)
        m_cash.return_value = {"total_balance": Decimal("5000000.00"), "active_accounts_count": 1}
        m_cash_curr.return_value = [
            {"currency": "COP", "total_balance": Decimal("5000000.00"), "active_accounts_count": 1}
        ]
        m_cf.return_value = {
            "income_current_period": Decimal("5000000.00"),
            "cash_expenses_current_period": Decimal("500000.00"),
            "credit_card_consumption_current_period": Decimal("2000000.00"),
            "debt_payments_current_period": Decimal("500000.00"),
            "goal_contributions_current_period": Decimal("0.00"),
            "obligation_payments_current_period": Decimal("0.00"),
        }
        m_cf_curr.return_value = [{"currency": "COP", **m_cf.return_value}]
        m_hcf.return_value = {
            "historical_income": Decimal("0.00"),
            "historical_expenses": Decimal("0.00"),
        }
        m_goals.return_value = {
            "active_goals_count": 0,
            "total_target": Decimal("0.00"),
            "total_saved": Decimal("0.00"),
        }
        m_obl_metrics.return_value = {"pending_count": 0, "pending_amount": Decimal("0.00")}
        m_transfers.return_value = {"monthly_transfer_volume": Decimal("0.00")}
        m_rec.return_value = []

        mock_obl_repo_class.return_value.get_pending_period_amounts_for_snapshot = AsyncMock(
            return_value={"COP": Decimal("0.00")}
        )
        mock_goal_repo_class.return_value.list_active = AsyncMock(return_value=[])
        mock_goal_repo_class.return_value.calculate_reserved_by_user_currency = AsyncMock(
            return_value={"COP": Decimal("0.00")}
        )

        snapshot = await service.get_snapshot(user_id)

        assert isinstance(snapshot, IntelligenceSnapshotRead)
        assert snapshot.debt.credit_card_total_debt == Decimal("3000000.00")
        assert snapshot.debt.billed_debt == Decimal("1000000.00")
        assert snapshot.debt.unbilled_debt == Decimal("2000000.00")
        assert snapshot.debt.payment_required == Decimal("1000000.00")
        assert snapshot.truth.payment_required == Decimal("1000000.00")
        # Free money accounts for billed credit debt in committed outflows
        assert snapshot.truth.free_money == Decimal("5000000.00") - Decimal("1000000.00")


@pytest.mark.asyncio
async def test_intelligence_get_debt_and_credit_cards_derives_from_canonical_facts():
    """Validates that get_debt and get_credit_cards build IntelligenceCreditCardRead

    exclusively from canonical CreditSummaryRead without consuming stale current_debt.
    """
    session = AsyncMock()
    service = IntelligenceService(session=session)
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()

    mock_status = CreditCardStatusRead(
        card_id=card_id,
        name="Mastercard Black",
        credit_limit=Decimal("15000000.00"),
        total_debt=Decimal("4500000.00"),
        billed_debt=Decimal("1500000.00"),
        unbilled_debt=Decimal("3000000.00"),
        available_credit=Decimal("10500000.00"),
        payment_required=Decimal("1500000.00"),
        minimum_payment=None,
        next_payment_estimate=Decimal("1500000.00"),
        statement_balance=Decimal("1500000.00"),
        monthly_cc_payment=Decimal("1500000.00"),
        cutoff_day=20,
        payment_due_day=10,
        next_payment_due_date="2026-11-10",
        purchases_count=3,
        payments_count=0,
        data_quality={"source": "canonical_closure"},
    )
    mock_summary = CreditSummaryRead(
        total_credit_limit=Decimal("15000000.00"),
        total_debt=Decimal("4500000.00"),
        total_available_credit=Decimal("10500000.00"),
        total_monthly_cc_payment=Decimal("1500000.00"),
        total_payment_required=Decimal("1500000.00"),
        total_next_payment_estimate=Decimal("1500000.00"),
        cards=[mock_status],
    )

    with (
        patch("app.credit.service.CreditCardService") as mock_cc_service_class,
        patch.object(service.repo, "get_pending_obligations", new_callable=AsyncMock) as m_obl,
    ):
        mock_cc_service_class.return_value.get_credit_summary = AsyncMock(return_value=mock_summary)
        m_obl.return_value = []

        debt_read = await service.get_debt(user_id)
        assert isinstance(debt_read, IntelligenceDebtRead)
        assert debt_read.total_estimated_credit_card_debt == Decimal("4500000.00")
        assert len(debt_read.credit_cards) == 1

        card = debt_read.credit_cards[0]
        assert isinstance(card, IntelligenceCreditCardRead)
        assert card.credit_card_id == card_id
        assert card.credit_limit == Decimal("15000000.00")
        assert card.billed_debt == Decimal("1500000.00")
        assert card.unbilled_debt == Decimal("3000000.00")
        assert card.available_credit == Decimal("10500000.00")
        assert card.estimated_available_credit == Decimal("10500000.00")
        assert card.payment_required == Decimal("1500000.00")
        assert card.statement_balance == Decimal("1500000.00")
        assert card.data_quality == {"source": "canonical_closure"}

        cards_list = await service.get_credit_cards(user_id)
        assert len(cards_list) == 1
        assert cards_list[0].credit_card_id == card_id


# ==============================================================================
# 2. Conversational Credit Consumer Tests (Acceptance Criterion 2)
# ==============================================================================


@pytest.mark.asyncio
async def test_conversational_read_ask_debt_renders_canonical_facts():
    """Validates that conversational ask_debt prefers canonical total_debt, identifies Nexum-modeled
    available credit provenance, falls back to legacy aliases when total_debt is absent, and avoids literal \\n."""
    # 1. Canonical card with total_debt
    data_canonical = {
        "total_debt": "4500000.00",
        "credit_cards": [
            {
                "credit_card_name": "Visa Oro",
                "total_debt": "4500000.00",
                "current_debt": "4500000.00",
                "billed_debt": "1500000.00",
                "unbilled_debt": "3000000.00",
                "payment_required": "1500000.00",
                "next_payment_estimate": "1500000.00",
                "available_credit": "5500000.00",
                "next_payment_due_date": "2026-11-05",
            }
        ],
    }
    rendered = render_read_response("ask_debt", data_canonical)
    assert "Tu deuda total de tarjetas de crédito es de $4500000.00." in rendered
    assert "- Visa Oro: Deuda total $4500000.00" in rendered
    assert "(Facturado: $1500000.00, Sin facturar: $3000000.00)." in rendered
    assert "Pago requerido: $1500000.00." in rendered
    assert "Cupo disponible (estimado Nexum): $5500000.00." in rendered
    assert "Próximo pago: 2026-11-05." in rendered
    assert "\\n" not in rendered
    assert "\n" in rendered

    # 2. Legacy card without total_debt (fallback compatibility)
    data_legacy = {
        "total_estimated_credit_card_debt": "2000000.00",
        "credit_cards": [
            {
                "credit_card_name": "Mastercard Clásica",
                "current_debt": "2000000.00",
                "estimated_available_credit": "3000000.00",
            }
        ],
    }
    rendered_legacy = render_read_response("ask_debt", data_legacy)
    assert "Tu deuda total de tarjetas de crédito es de $2000000.00." in rendered_legacy
    assert "- Mastercard Clásica: Deuda total $2000000.00" in rendered_legacy
    assert "Cupo disponible (estimado Nexum): $3000000.00." in rendered_legacy


@pytest.mark.asyncio
async def test_conversational_read_ask_financial_snapshot_calls_get_snapshot_without_attribute_error():
    """Validates that conversational ask_financial_snapshot calls get_snapshot cleanly

    and consumes canonical snapshot truth without crashing on missing repository views.
    """
    mock_session = AsyncMock()
    mock_intel_service = AsyncMock()
    user_id = uuid.uuid4()
    trace_id = uuid.uuid4()

    mock_intel_service.get_snapshot.return_value = IntelligenceSnapshotRead(
        period={
            "month": "2026-10",
            "start_date": "2026-10-01",
            "end_date": "2026-11-01",
            "timezone": "America/Bogota",
        },
        cash={"total_balance": Decimal("5000000.00"), "active_accounts_count": 1},
        cashflow={
            "income": Decimal("6000000.00"),
            "expenses": Decimal("2000000.00"),
            "net_cashflow": Decimal("4000000.00"),
            "income_current_period": Decimal("6000000.00"),
            "cash_expenses_current_period": Decimal("2000000.00"),
            "credit_card_consumption_current_period": Decimal("1000000.00"),
            "debt_payments_current_period": Decimal("500000.00"),
            "goal_contributions_current_period": Decimal("0.00"),
            "obligation_payments_current_period": Decimal("0.00"),
            "committed_outflows_current_period": Decimal("500000.00"),
            "net_cashflow_current_period": Decimal("4000000.00"),
        },
        historical={
            "historical_income": Decimal("0.00"),
            "historical_expenses": Decimal("0.00"),
            "historical_net_cashflow": Decimal("0.00"),
        },
        debt=SnapshotDebt(
            credit_card_total_debt=Decimal("2500000.00"),
            billed_debt=Decimal("1000000.00"),
            unbilled_debt=Decimal("1500000.00"),
            payment_required=Decimal("1000000.00"),
            next_payment_estimate=Decimal("1000000.00"),
        ),
        goals={
            "active_goals_count": 0,
            "total_target": Decimal("0.00"),
            "total_saved": Decimal("0.00"),
        },
        obligations={"pending_count": 0, "pending_amount": Decimal("0.00")},
        transfers={"monthly_transfer_volume": Decimal("0.00")},
        recent_activity=[],
        truth=SnapshotTruth(
            available_real=Decimal("5000000.00"),
            committed_outflows=Decimal("1000000.00"),
            free_money=Decimal("4000000.00"),
            safe_money=Decimal("4000000.00"),
            payment_required=Decimal("1000000.00"),
            goals_required_this_period=Decimal("0.00"),
            calculation_warnings=[],
            data_quality={},
        ),
    )

    conv_service = ConversationsService(
        session=mock_session,
        intel_service=mock_intel_service,
        cash_service=AsyncMock(),
        goals_service=AsyncMock(),
        obl_service=AsyncMock(),
        credit_service=AsyncMock(),
        transfers_service=AsyncMock(),
    )
    conv_service.repo = AsyncMock()
    conv_service.repo.save_message.return_value = uuid.uuid4()
    conv_service.repo.get_message_by_id.return_value = {"message": "pago anticipado"}

    req = ConversationalRequest(message="¿Cómo está mi resumen financiero?", channel="api")
    resp = await conv_service._process_read_intent(
        user_id=user_id,
        intent="ask_financial_snapshot",
        req=req,
        msg_id=uuid.uuid4(),
        trace_id=trace_id,
    )

    assert resp.status == "completed"
    assert resp.intent == "ask_financial_snapshot"
    assert "Dinero libre: $4000000.00" in resp.response_text
    assert "Deuda TC: $2500000.00" in resp.response_text
    mock_intel_service.get_snapshot.assert_called_once_with(user_id)


@pytest.mark.asyncio
async def test_conversational_write_purchase_delegates_to_credit_service_and_preserves_idempotency():
    """Validates that create_credit_card_purchase delegates to CreditCardService.create_purchase

    with identical command_id and DTO without duplicating logic.
    """
    mock_session = AsyncMock()
    mock_credit_service = AsyncMock()
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    action_id = uuid.uuid4()
    command_id = uuid.uuid4()
    now = datetime.now(UTC)

    conv_service = ConversationsService(
        session=mock_session,
        intel_service=AsyncMock(),
        cash_service=AsyncMock(),
        goals_service=AsyncMock(),
        obl_service=AsyncMock(),
        credit_service=mock_credit_service,
        transfers_service=AsyncMock(),
    )
    conv_service.repo = AsyncMock()
    conv_service.repo.save_message.return_value = uuid.uuid4()
    conv_service.repo.get_message_by_id.return_value = {"message": "pago anticipado"}

    action = PendingActionRead(
        id=action_id,
        user_id=user_id,
        intent="create_credit_card_purchase",
        data={
            "amount": 250000.00,
            "credit_card_id": str(card_id),
            "installments_total": 3,
            "_cc_name": "Visa",
        },
        status="awaiting_confirmation",
        command_id=command_id,
        source_message_id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        expires_at=now,
    )
    conv_service.repo.get_pending_action.return_value = action
    conv_service.repo.get_message_by_id.return_value = {
        "message": "Compré 250 mil a 3 cuotas con la Visa"
    }

    req = ConversationalRequest(message="sí", channel="api", pending_action_id=str(action_id))
    resp = await conv_service._handle_pending_action_flow(
        user_id=user_id,
        request=req,
        trace_id=uuid.uuid4(),
        open_actions=[action],
    )

    assert resp.status == "completed"
    assert resp.response_text == "¡Operación registrada con éxito!"

    mock_credit_service.create_purchase.assert_called_once()
    call_args = mock_credit_service.create_purchase.call_args[0]
    assert call_args[0] == user_id
    assert call_args[1] == card_id
    dto = call_args[2]
    assert isinstance(dto, CreditCardPurchaseCreate)
    assert dto.amount == Decimal("250000.0")
    assert dto.installments_total == 3
    assert call_args[3] == str(command_id)


@pytest.mark.asyncio
async def test_conversational_write_payment_delegates_to_credit_service_and_preserves_invariants():
    """Validates that create_credit_card_payment delegates to CreditCardService.create_payment

    preserving Account, Card, amount, and command_id.
    """
    mock_session = AsyncMock()
    mock_credit_service = AsyncMock()
    user_id = uuid.uuid4()
    card_id = uuid.uuid4()
    account_id = uuid.uuid4()
    action_id = uuid.uuid4()
    command_id = uuid.uuid4()
    now = datetime.now(UTC)

    conv_service = ConversationsService(
        session=mock_session,
        intel_service=AsyncMock(),
        cash_service=AsyncMock(),
        goals_service=AsyncMock(),
        obl_service=AsyncMock(),
        credit_service=mock_credit_service,
        transfers_service=AsyncMock(),
    )
    conv_service.repo = AsyncMock()
    conv_service.repo.save_message.return_value = uuid.uuid4()
    conv_service.repo.get_message_by_id.return_value = {"message": "pago anticipado"}

    action = PendingActionRead(
        id=action_id,
        user_id=user_id,
        intent="create_credit_card_payment",
        data={
            "amount": 500000.00,
            "credit_card_id": str(card_id),
            "account_id": str(account_id),
            "_cc_name": "Visa",
            "_account_name": "Nequi",
        },
        status="awaiting_confirmation",
        command_id=command_id,
        source_message_id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        expires_at=now,
    )
    conv_service.repo.get_pending_action.return_value = action
    conv_service.repo.get_message_by_id.return_value = {
        "message": "Paga 500 mil a la Visa desde Nequi"
    }

    req = ConversationalRequest(message="sí", channel="api", pending_action_id=str(action_id))
    resp = await conv_service._handle_pending_action_flow(
        user_id=user_id,
        request=req,
        trace_id=uuid.uuid4(),
        open_actions=[action],
    )

    assert resp.status == "completed"
    assert resp.response_text == "¡Operación registrada con éxito!"

    mock_credit_service.create_payment.assert_called_once()
    call_args = mock_credit_service.create_payment.call_args[0]
    assert call_args[0] == user_id
    assert call_args[1] == card_id
    dto = call_args[2]
    assert isinstance(dto, CreditCardPaymentCreate)
    assert dto.amount == Decimal("500000.0")
    assert dto.account_id == account_id
    assert call_args[3] == command_id


@pytest.mark.asyncio
async def test_conversational_unsupported_credit_intent_fails_closed():
    """Validates that unsupported conversational write operations (such as early payments or adjustments)

    are cleanly rejected with UnsupportedConversationalIntentError and status 'error'.
    """
    mock_session = AsyncMock()
    user_id = uuid.uuid4()
    action_id = uuid.uuid4()
    now = datetime.now(UTC)

    conv_service = ConversationsService(
        session=mock_session,
        intel_service=AsyncMock(),
        cash_service=AsyncMock(),
        goals_service=AsyncMock(),
        obl_service=AsyncMock(),
        credit_service=AsyncMock(),
        transfers_service=AsyncMock(),
    )
    conv_service.repo = AsyncMock()
    conv_service.repo.save_message.return_value = uuid.uuid4()
    conv_service.repo.get_message_by_id.return_value = {"message": "pago anticipado"}

    action = PendingActionRead(
        id=action_id,
        user_id=user_id,
        intent="create_credit_card_early_payment",  # Unsupported intent
        data={"amount": 100000.00},
        status="awaiting_confirmation",
        command_id=uuid.uuid4(),
        source_message_id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        expires_at=now,
    )
    conv_service.repo.get_pending_action.return_value = action

    req = ConversationalRequest(message="sí", channel="api", pending_action_id=str(action_id))
    resp = await conv_service._handle_pending_action_flow(
        user_id=user_id,
        request=req,
        trace_id=uuid.uuid4(),
        open_actions=[action],
    )

    assert resp.status == "error"
    assert "Todavía no puedo ejecutar esa operación automáticamente" in resp.response_text


# ==============================================================================
# 3. Stable Domain Error Codes Contract Tests (Acceptance Criterion 4)
# ==============================================================================


def test_credit_stable_error_codes_contract():
    """Verifies that all required credit domain error classes expose exact stable error codes

    and HTTP status codes matching project standards.
    """
    # 1. Insufficient spendable balance / Goal reservation breach
    err_goal = CreditGoalReservationBreachError()
    assert err_goal.error_code == "credit_goal_reservation_breach"
    assert err_goal.status_code == 409

    err_funds = InsufficientFundsError()
    assert err_funds.error_code == "insufficient_funds"
    assert err_funds.status_code == 409

    # 2. Limit exceeded
    err_limit = CreditLimitExceededError()
    assert err_limit.error_code == "credit_limit_exceeded"
    assert err_limit.status_code == 409

    # 3. Voluntary overpayment
    err_overpay = InvalidPaymentAmountError()
    assert err_overpay.error_code == "credit_payment_amount_invalid"
    assert err_overpay.status_code == 400

    # 4. Ownership
    err_forbidden = CreditCardForbiddenError()
    assert err_forbidden.error_code == "credit_card_forbidden"
    assert err_forbidden.status_code == 403

    err_not_found = CreditCardNotFoundError()
    assert err_not_found.error_code == "credit_card_not_found"
    assert err_not_found.status_code == 404

    # 5. Inactive Card / Account
    err_card_inactive = CreditCardInactiveError()
    assert err_card_inactive.error_code == "credit_card_inactive"
    assert err_card_inactive.status_code == 400

    err_acc_inactive = CreditAccountInactiveError()
    assert err_acc_inactive.error_code == "account_inactive"
    assert err_acc_inactive.status_code == 403
    assert isinstance(err_acc_inactive, AccountForbiddenError)

    # 6. Idempotency conflict
    err_idemp = CreditIdempotencyConflictError()
    assert err_idemp.error_code == "credit_idempotency_conflict"
    assert err_idemp.status_code == 409

    # 7. Invalid lifecycle / refund / reversal
    err_lifecycle = CreditLifecycleInvalidityError()
    assert err_lifecycle.error_code == "credit_lifecycle_invalidity"
    assert err_lifecycle.status_code == 400

    err_alloc = CreditAllocationError()
    assert err_alloc.error_code == "credit_allocation_error"
    assert err_alloc.status_code == 409

    err_cycle_closed = CreditBillingCycleClosedError()
    assert err_cycle_closed.error_code == "credit_billing_cycle_closed"
    assert err_cycle_closed.status_code == 409

    err_stmt_immut = CreditStatementImmutableError()
    assert err_stmt_immut.error_code == "credit_statement_immutable"
    assert err_stmt_immut.status_code == 409

    err_ceil = CreditInstallmentCeilingError()
    assert err_ceil.error_code == "credit_installment_ceiling_error"
    assert err_ceil.status_code == 409

    err_boundary = CreditActivationBoundaryError()
    assert err_boundary.error_code == "credit_activation_boundary_violation"
    assert err_boundary.status_code == 400

    # 8. Unsupported FX
    err_unsupp_fx = CreditUnsupportedCurrencyError()
    assert err_unsupp_fx.error_code == "unsupported_currency"
    assert err_unsupp_fx.status_code == 400

    err_rate_unavail = CreditFXRateUnavailableError()
    assert err_rate_unavail.error_code == "fx_rate_unavailable"
    assert err_rate_unavail.status_code == 503


# ==============================================================================
# 4. OpenAPI Contract & Deprecation Markers (Acceptance Criteria 3 & 6)
# ==============================================================================


def test_openapi_credit_schema_definitions_and_deprecation_markers():
    """Validates that OpenAPI serialization exports proper descriptions and 'deprecated: True'

    markers on legacy aliases while exposing canonical definitions.
    """
    card_schema = CreditCardRead.model_json_schema(mode="serialization")
    props = card_schema["properties"]

    # Legacy aliases are marked deprecated
    assert props["current_debt"].get("deprecated") is True
    assert props["estimated_current_debt"].get("deprecated") is True
    assert props["monthly_cc_payment"].get("deprecated") is True
    assert props["estimated_available_credit"].get("deprecated") is True

    # Canonical facts are clearly described
    assert "total_debt" in props
    assert "billed_debt" in props
    assert "unbilled_debt" in props
    assert "available_credit" in props
    assert "minimum_payment" in props
    assert "next_payment_estimate" in props
    assert "statement_balance" in props

    purchase_in_schema = CreditCardPurchaseCreate.model_json_schema()
    p_props = purchase_in_schema["properties"]
    assert p_props["amount"].get("deprecated") is True
    assert "original_amount" in p_props
    assert "original_currency" in p_props
    assert "currency" in p_props

    payment_read_schema = CreditCardPaymentRead.model_json_schema(mode="serialization")
    pay_props = payment_read_schema["properties"]
    assert pay_props["amount"].get("deprecated") is True
    assert pay_props["currency"].get("deprecated") is True
    assert "booked_amount" in pay_props
    assert "booked_currency" in pay_props
    assert "principal_applied" in pay_props
    assert "interest_applied" in pay_props
    assert "fee_applied" in pay_props
    assert "unallocated_amount" in pay_props
    assert "provenance" in pay_props
    assert "allocation_source" in pay_props

    adjustment_read_schema = CreditCardAdjustmentRead.model_json_schema(mode="serialization")
    adj_props = adjustment_read_schema["properties"]
    assert adj_props["amount"].get("deprecated") is True
    assert adj_props["currency"].get("deprecated") is True
    assert "adjustment_type" in adj_props
    assert "direction" in adj_props
    assert "booked_amount" in adj_props
    assert "provenance" in adj_props
    assert "allocation_source" in adj_props


def test_openapi_cycle_and_statement_schemas():
    """Validates that statement and cycle contracts are fully present in OpenAPI schemas."""
    cycle_schema = CreditCardBillingCycleRead.model_json_schema()
    c_props = cycle_schema["properties"]
    assert "start_date" in c_props
    assert "cutoff_date" in c_props
    assert "due_date" in c_props
    assert "billing_period" in c_props

    stmt_schema = CreditCardStatementRead.model_json_schema()
    s_props = stmt_schema["properties"]
    assert "statement_balance" in s_props
    assert "payments_received" in s_props
    assert "minimum_payment" in s_props
    assert "components" in s_props

    comp_schema = CreditCardStatementComponentRead.model_json_schema()
    comp_props = comp_schema["properties"]
    assert "component_type" in comp_props
    assert "principal_amount" in comp_props
    assert "interest_amount" in comp_props
    assert "fee_amount" in comp_props
    assert "amount" in comp_props

    alloc_schema = CreditCardPaymentAllocationRead.model_json_schema()
    alloc_props = alloc_schema["properties"]
    assert "allocated_principal" in alloc_props
    assert "allocated_interest" in alloc_props
    assert "allocated_fees" in alloc_props
    assert "allocation_order" in alloc_props
    assert "allocation_source" in alloc_props


@pytest.mark.asyncio
async def test_credit_api_router_error_envelope_contracts():
    """Validates that credit router endpoints preserve the exact Nexum domain error envelope
    {"ok": false, "error_code": "...", "message": "...", "detail": {}}
    and return the exact HTTP status codes rather than converting them to generic HTTPExceptions.
    """
    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_db_session
    from app.credit.router import get_credit_service
    from app.main import app
    from app.users.dependencies import get_current_user_profile_dep
    from app.users.schemas import UserRead

    user_id = uuid.uuid4()
    mock_user = UserRead(
        id=user_id,
        email="credit_test@nexum.app",
        name="Credit Test User",
        timezone="America/Bogota",
        currency="COP",
        status="active",
    )
    mock_service = AsyncMock()
    mock_session = AsyncMock()

    app.dependency_overrides[get_current_user_profile_dep] = lambda: mock_user
    app.dependency_overrides[get_credit_service] = lambda: mock_service
    app.dependency_overrides[get_db_session] = lambda: mock_session

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            card_id = uuid.uuid4()
            cmd_header = {"Idempotency-Key": str(uuid.uuid4())}

            # 1. CreditCardNotFoundError -> 404, credit_card_not_found
            mock_service.get_card.side_effect = CreditCardNotFoundError()
            resp = await client.get(f"/api/v1/credit/cards/{card_id}")
            assert resp.status_code == 404
            data = resp.json()
            assert data["ok"] is False
            assert data["error_code"] == "credit_card_not_found"

            # 2. CreditCardInactiveError -> 400, credit_card_inactive
            mock_service.create_purchase.side_effect = CreditCardInactiveError()
            resp = await client.post(
                f"/api/v1/credit/cards/{card_id}/purchases",
                json={"original_amount": "100.00", "original_currency": "USD"},
                headers=cmd_header,
            )
            assert resp.status_code == 400
            data = resp.json()
            assert data["ok"] is False
            assert data["error_code"] == "credit_card_inactive"

            # 3. CreditLimitExceededError -> 409, credit_limit_exceeded
            mock_service.create_purchase.side_effect = CreditLimitExceededError()
            resp = await client.post(
                f"/api/v1/credit/cards/{card_id}/purchases",
                json={"original_amount": "100.00", "original_currency": "USD"},
                headers=cmd_header,
            )
            assert resp.status_code == 409
            data = resp.json()
            assert data["ok"] is False
            assert data["error_code"] == "credit_limit_exceeded"

            # 4. CreditGoalReservationBreachError -> 409, credit_goal_reservation_breach
            mock_service.create_payment.side_effect = CreditGoalReservationBreachError()
            resp = await client.post(
                f"/api/v1/credit/cards/{card_id}/payments",
                json={"account_id": str(uuid.uuid4()), "amount": "50000.00"},
                headers=cmd_header,
            )
            assert resp.status_code == 409
            data = resp.json()
            assert data["ok"] is False
            assert data["error_code"] == "credit_goal_reservation_breach"

            # 5. InsufficientFundsError -> 409, insufficient_funds
            mock_service.create_payment.side_effect = InsufficientFundsError()
            resp = await client.post(
                f"/api/v1/credit/cards/{card_id}/payments",
                json={"account_id": str(uuid.uuid4()), "amount": "50000.00"},
                headers=cmd_header,
            )
            assert resp.status_code == 409
            data = resp.json()
            assert data["ok"] is False
            assert data["error_code"] == "insufficient_funds"

            # 6. InvalidPaymentAmountError -> 400, credit_payment_amount_invalid
            mock_service.create_payment.side_effect = InvalidPaymentAmountError(
                message="Voluntary overpayment above total payable debt is not permitted"
            )
            resp = await client.post(
                f"/api/v1/credit/cards/{card_id}/payments",
                json={"account_id": str(uuid.uuid4()), "amount": "5000000.00"},
                headers=cmd_header,
            )
            assert resp.status_code == 400
            data = resp.json()
            assert data["ok"] is False
            assert data["error_code"] == "credit_payment_amount_invalid"

            # 7. CreditAccountInactiveError -> 403, account_inactive
            mock_service.create_payment.side_effect = CreditAccountInactiveError()
            resp = await client.post(
                f"/api/v1/credit/cards/{card_id}/payments",
                json={"account_id": str(uuid.uuid4()), "amount": "50000.00"},
                headers=cmd_header,
            )
            assert resp.status_code == 403
            data = resp.json()
            assert data["ok"] is False
            assert data["error_code"] == "account_inactive"

            # 8. CreditIdempotencyConflictError -> 409, credit_idempotency_conflict
            mock_service.create_payment.side_effect = CreditIdempotencyConflictError()
            resp = await client.post(
                f"/api/v1/credit/cards/{card_id}/payments",
                json={"account_id": str(uuid.uuid4()), "amount": "50000.00"},
                headers=cmd_header,
            )
            assert resp.status_code == 409
            data = resp.json()
            assert data["ok"] is False
            assert data["error_code"] == "credit_idempotency_conflict"

            # 9. CreditBillingCycleClosedError -> 409, credit_billing_cycle_closed
            mock_service.create_purchase.side_effect = CreditBillingCycleClosedError()
            resp = await client.post(
                f"/api/v1/credit/cards/{card_id}/purchases",
                json={"original_amount": "100.00", "original_currency": "USD"},
                headers=cmd_header,
            )
            assert resp.status_code == 409
            data = resp.json()
            assert data["ok"] is False
            assert data["error_code"] == "credit_billing_cycle_closed"

            # 10. CreditLifecycleInvalidityError -> 400, credit_lifecycle_invalidity
            mock_service.create_adjustment.side_effect = CreditLifecycleInvalidityError()
            resp = await client.post(
                f"/api/v1/credit/cards/{card_id}/adjustments",
                json={
                    "original_transaction_id": str(uuid.uuid4()),
                    "amount": "10000.00",
                    "adjustment_type": "refund",
                    "direction": "credit",
                },
                headers=cmd_header,
            )
            assert resp.status_code == 400
            data = resp.json()
            assert data["ok"] is False
            assert data["error_code"] == "credit_lifecycle_invalidity"

            # 11. CreditFXRateUnavailableError -> 503, fx_rate_unavailable
            mock_service.create_purchase.side_effect = CreditFXRateUnavailableError()
            resp = await client.post(
                f"/api/v1/credit/cards/{card_id}/purchases",
                json={"original_amount": "100.00", "original_currency": "USD"},
                headers=cmd_header,
            )
            assert resp.status_code == 503
            data = resp.json()
            assert data["ok"] is False
            assert data["error_code"] == "fx_rate_unavailable"

            # 12. CreditUnsupportedCurrencyError -> 400, unsupported_currency
            mock_service.create_purchase.side_effect = CreditUnsupportedCurrencyError()
            resp = await client.post(
                f"/api/v1/credit/cards/{card_id}/purchases",
                json={"original_amount": "100.00", "original_currency": "XYZ"},
                headers=cmd_header,
            )
            assert resp.status_code == 400
            data = resp.json()
            assert set(data.keys()) >= {"ok", "error_code", "message", "detail"}
            assert data["ok"] is False
            assert data["error_code"] == "unsupported_currency"
            assert (
                "data" not in data
            )  # Contract: root-level envelope, NOT wrapped in data.error_code
    finally:
        app.dependency_overrides.clear()


def test_committed_openapi_matches_runtime_app_openapi():
    """Validates that committed openapi.json matches app.openapi() serialization exactly with zero drift."""
    import json
    from pathlib import Path

    from app.main import app

    root_dir = Path(__file__).resolve().parent.parent.parent
    openapi_file = root_dir / "openapi.json"
    assert openapi_file.exists(), "openapi.json must exist in repository root"

    committed_content = openapi_file.read_text(encoding="utf-8")
    runtime_content = json.dumps(app.openapi(), indent=2) + "\n"
    assert committed_content == runtime_content, (
        "Committed openapi.json does not match runtime app.openapi() serialization. "
        "Run 'python scripts/dev/export_openapi.py' to regenerate."
    )


def test_frontend_handoff_document_exact_schema_contracts():
    """Validates that the Frontend Handoff document uses exact canonical schema field names

    from runtime OpenAPI and forbids known invented aliases.
    """
    from pathlib import Path

    from app.main import app

    root_dir = Path(__file__).resolve().parent.parent.parent
    handoff_file = (
        root_dir / "docs" / "handoff" / "backend" / "credit-canonical-v2-frontend-handoff.md"
    )
    assert handoff_file.exists(), f"Handoff file {handoff_file} must exist"

    content = handoff_file.read_text(encoding="utf-8")

    # 1. Assert forbidden invented aliases are completely absent
    forbidden_aliases = [
        "billing_cycle_id",
        "statement_period",
        "total_installments",
        "source_account_id",
        "target_purchase_id",
        "data.error_code",
    ]
    for alias in forbidden_aliases:
        assert alias not in content, (
            f"Forbidden invented alias '{alias}' was found in the frontend handoff document. "
            "Use exact canonical schema names from openapi.json."
        )

    # 2. Assert canonical schema property names are documented
    canonical_statement_fields = [
        "billing_period",
        "cycle_id",
        "billing_period_start",
        "cutoff_date",
        "due_date",
        "components",
        "facts",
    ]
    for field in canonical_statement_fields:
        assert f"`{field}`" in content, (
            f"Canonical statement field '{field}' must be documented in handoff"
        )

    canonical_installment_fields = [
        "installments_total",
        "scheduled_due_date",
        "assessed_interest_amount",
        "estimated_interest_amount",
        "original_amount",
        "original_currency",
        "booked_amount",
        "booked_currency",
    ]
    for field in canonical_installment_fields:
        assert f"`{field}`" in content, (
            f"Canonical installment field '{field}' must be documented in handoff"
        )

    # 3. Assert CreditSummaryRead canonical fields are documented
    for field in ["total_debt", "total_credit_limit", "total_available_credit"]:
        assert f"`{field}`" in content, (
            f"Canonical CreditSummaryRead field '{field}' must be documented in handoff"
        )

    # 4. Verify against runtime OpenAPI schemas
    openapi_schemas = app.openapi()["components"]["schemas"]
    statement_props = set(openapi_schemas["CreditCardStatementRead"]["properties"].keys())
    for field in canonical_statement_fields:
        assert field in statement_props, (
            f"Field '{field}' must exist in runtime CreditCardStatementRead"
        )

    installment_props = set(openapi_schemas["CreditCardInstallmentRead"]["properties"].keys())
    for field in canonical_installment_fields:
        assert field in installment_props, (
            f"Field '{field}' must exist in runtime CreditCardInstallmentRead"
        )
