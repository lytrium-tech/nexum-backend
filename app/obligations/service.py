from uuid import UUID

from app.accounts.exceptions import AccountForbiddenError
from app.accounts.repository import AccountRepository
from app.cash.exceptions import InsufficientFundsError
from app.core.errors import NotFoundError
from app.core.utils import clean_presentation_name, normalize_name
from app.ledger.enums import Direction, EventType
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate
from app.obligations.exceptions import (
    ObligationAlreadyPaidError,
    ObligationAmountMismatchError,
    ObligationForbiddenError,
    ObligationInactiveError,
    ObligationOverpaymentError,
)
from app.obligations.models import Obligation, ObligationPayment
from app.obligations.repository import ObligationRepository
from app.obligations.schemas import (
    ObligationCreate,
    ObligationPaymentCreate,
    ObligationPaymentResult,
    ObligationRead,
    ObligationUpdate,
)


class ObligationService:
    def __init__(
        self,
        repository: ObligationRepository,
        account_repo: AccountRepository,
        ledger_repo: LedgerRepository,
    ):
        self.repository = repository
        self.account_repo = account_repo
        self.ledger_repo = ledger_repo

    def _current_period(self) -> str:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Bogota")
        now = datetime.now(tz)
        return f"{now.year}-{now.month:02d}"

    async def list_obligations(self, auth_user_id: UUID) -> list[ObligationRead]:
        from decimal import Decimal
        obligations = await self.repository.list_active(auth_user_id)
        period = self._current_period()
        payments = await self.repository.get_period_payments(auth_user_id, period)
        
        results = []
        for o in obligations:
            o_read = ObligationRead.model_validate(o)
            o_read.paid_this_period = payments.get(o.id, Decimal("0.00"))
            results.append(o_read)
        return results

    async def get_obligation(self, auth_user_id: UUID, obligation_id: UUID) -> ObligationRead:
        from decimal import Decimal
        obligation = await self._get_obligation_or_404(obligation_id)
        if obligation.user_id != auth_user_id:
            raise ObligationForbiddenError()
            
        period = self._current_period()
        payments = await self.repository.get_period_payments(auth_user_id, period)
        
        o_read = ObligationRead.model_validate(obligation)
        o_read.paid_this_period = payments.get(obligation.id, Decimal("0.00"))
        return o_read

    async def create_obligation(
        self, auth_user_id: UUID, payload: ObligationCreate
    ) -> ObligationRead:
        from decimal import Decimal
        norm_name = normalize_name(payload.name)
        if not norm_name:
            raise ValueError("El nombre no puede estar vacío.")

        exists = await self.repository.check_name_exists(auth_user_id, norm_name)
        if exists:
            raise ValueError("Ya existe una obligación activa con este nombre.")

        db_obligation = Obligation(
            user_id=auth_user_id,
            name=clean_presentation_name(payload.name),
            amount=payload.amount,
            payment_mode=payload.payment_mode,
            due_day=payload.due_day,
            frequency=payload.frequency,
            category_id=payload.category_id,
            is_active=True,
            metadata_=payload.metadata,
        )
        created = await self.repository.create(db_obligation)
        await self.repository.session.refresh(created)
        
        o_read = ObligationRead.model_validate(created)
        o_read.paid_this_period = Decimal("0.00")
        return o_read

    async def update_obligation(
        self, auth_user_id: UUID, obligation_id: UUID, payload: ObligationUpdate
    ) -> ObligationRead:
        from decimal import Decimal
        obligation = await self._get_obligation_or_404(obligation_id)
        if obligation.user_id != auth_user_id:
            raise ObligationForbiddenError()

        if payload.name is not None:
            norm_name = normalize_name(payload.name)
            if not norm_name:
                raise ValueError("El nombre no puede estar vacío.")
            if normalize_name(obligation.name) != norm_name:
                exists = await self.repository.check_name_exists(auth_user_id, norm_name)
                if exists:
                    raise ValueError("Ya existe una obligación activa con este nombre.")
            obligation.name = clean_presentation_name(payload.name)

        if payload.amount is not None:
            obligation.amount = payload.amount
        if payload.payment_mode is not None:
            obligation.payment_mode = payload.payment_mode
        if payload.due_day is not None:
            obligation.due_day = payload.due_day
        if payload.frequency is not None:
            obligation.frequency = payload.frequency
        if payload.category_id is not None:
            obligation.category_id = payload.category_id
        if payload.metadata is not None:
            obligation.metadata_ = payload.metadata

        await self.repository.session.flush()
        
        period = self._current_period()
        payments = await self.repository.get_period_payments(auth_user_id, period)
        
        o_read = ObligationRead.model_validate(obligation)
        o_read.paid_this_period = payments.get(obligation.id, Decimal("0.00"))
        return o_read

    async def delete_obligation(self, auth_user_id: UUID, obligation_id: UUID) -> None:
        obligation = await self._get_obligation_or_404(obligation_id)
        if obligation.user_id != auth_user_id:
            raise ObligationForbiddenError()

        obligation.is_active = False
        await self.repository.session.flush()

    async def create_payment(
        self,
        auth_user_id: UUID,
        obligation_id: UUID,
        payload: ObligationPaymentCreate,
        idempotency_key: str | None,
    ) -> ObligationPaymentResult:
        from decimal import Decimal
        obligation = await self.repository.get_by_id_for_update(obligation_id)
        if not obligation:
            raise NotFoundError(message="Obligación no encontrada.")

        if obligation.user_id != auth_user_id:
            raise ObligationForbiddenError()

        if not obligation.is_active:
            raise ObligationInactiveError()

        period = self._current_period()
        payments = await self.repository.get_period_payments(auth_user_id, period)
        paid_this_period = payments.get(obligation.id, Decimal("0.00"))

        if obligation.payment_mode == "fixed_full_payment":
            if paid_this_period > 0:
                raise ObligationAlreadyPaidError()
            if obligation.amount is None or payload.amount != obligation.amount:
                raise ObligationAmountMismatchError(str(obligation.amount))
        elif obligation.payment_mode == "partial_allowed":
            if obligation.amount is not None:
                remaining = max(Decimal("0.00"), obligation.amount - paid_this_period)
                if remaining <= 0:
                    raise ObligationAlreadyPaidError()
                if payload.amount > remaining:
                    raise ObligationOverpaymentError(str(remaining))
        elif obligation.payment_mode == "variable_amount":
            pass  # variable_amount allows any amount, any number of payments

        account = await self.account_repo.get_by_id_for_update(payload.account_id)
        if not account:
            raise NotFoundError(message="Cuenta no encontrada.")

        if account.user_id is not None and account.user_id != auth_user_id:
            raise AccountForbiddenError()

        if account.balance < payload.amount:
            raise InsufficientFundsError()

        event_create = LedgerEventCreate(
            user_id=auth_user_id,
            account_id=account.id,
            event_type=EventType.OBLIGATION_PAYMENT,
            direction=Direction.OUTFLOW,
            amount=payload.amount,
            source_message_id=payload.source_message_id,
            raw_message=payload.raw_message,
            metadata={"obligation_id": str(obligation.id)},
        )

        if idempotency_key:
            try:
                event_create.command_id = UUID(idempotency_key)
            except ValueError:
                pass

        result = await self.ledger_repo.insert_event(event_create)
        event = result.event

        if result.idempotent:
            return ObligationPaymentResult(
                payment_id=None,
                event_id=event.id if event else None,
                amount=payload.amount,
                balance_after=account.balance,
                status="idempotent_retry",
            )

        await self.account_repo.update_balance(account, -payload.amount)

        payment = ObligationPayment(
            user_id=auth_user_id,
            obligation_id=obligation.id,
            amount=payload.amount,
            account_id=account.id,
            event_id=event.id,
            period=event.period,
        )
        await self.repository.create_payment(payment)

        if obligation.frequency == "once":
            obligation.is_active = False

        await self.repository.session.flush()
        return ObligationPaymentResult(
            payment_id=payment.id,
            event_id=event.id,
            amount=payload.amount,
            balance_after=account.balance,
            status="success",
        )

    async def _get_obligation_or_404(self, obligation_id: UUID) -> Obligation:
        obligation = await self.repository.get_by_id(obligation_id)
        if not obligation:
            raise NotFoundError(message="Obligación no encontrada.")
        return obligation
