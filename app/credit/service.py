import calendar
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.credit.exceptions import (
    CreditCardInactiveError,
    CreditCardNotFoundError,
    CreditLimitExceededError,
    InvalidPaymentAmountError,
)
from app.credit.models import (
    CreditCard,
    CreditCardInstallment,
    CreditCardTransaction,
)
from app.credit.repository import CreditCardRepository
from app.credit.schemas import (
    CreditCardCreate,
    CreditCardInstallmentRead,
    CreditCardPaymentCreate,
    CreditCardPaymentResult,
    CreditCardPurchaseCreate,
    CreditCardPurchaseResult,
    CreditCardRead,
    CreditCardStatusRead,
    CreditCardUpdate,
    CreditSummaryRead,
)
from app.credit.utils import calculate_credit_card_dates
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate
from app.ledger.service import LedgerService


class CreditCardService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = CreditCardRepository(session)
        self.ledger_service = LedgerService(LedgerRepository(session))
        self.account_repo = AccountRepository(session)

    def _data_quality(self) -> dict[str, str]:
        return {
            "current_debt": "computed_from_credit_card_transactions",
            "payment_required": "billed_debt",
            "next_payment_estimate": "estimated",
            "statement_balance": "not_available",
        }

    def _add_months(self, value: date, months: int) -> date:
        month = value.month - 1 + months
        year = value.year + month // 12
        month = month % 12 + 1
        day = min(value.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def _installment_amounts(self, amount: Decimal, total: int) -> list[Decimal]:
        quantized = amount.quantize(Decimal("0.01"))
        base = (quantized / Decimal(total)).quantize(Decimal("0.01"))
        amounts = [base for _ in range(total)]
        amounts[-1] = quantized - sum(amounts[:-1], Decimal("0.00"))
        return amounts

    def _normalize_command_id(self, command_id: uuid.UUID | str) -> uuid.UUID:
        return command_id if isinstance(command_id, uuid.UUID) else uuid.UUID(str(command_id))

    async def _build_installments(
        self,
        user_id: uuid.UUID,
        card: CreditCard,
        transaction_id: uuid.UUID,
        amount: Decimal,
        installments_total: int,
    ) -> list[CreditCardInstallment]:
        today = date.today()
        amounts = self._installment_amounts(amount, installments_total)
        installments = []
        for index, principal in enumerate(amounts, start=1):
            scheduled_date = self._add_months(today, index - 1)
            # Find cycle due date for the scheduled_date
            _, _, next_due = calculate_credit_card_dates(
                scheduled_date, card.cutoff_day, card.due_day
            )
            installments.append(
                CreditCardInstallment(
                    user_id=user_id,
                    credit_card_id=card.id,
                    purchase_transaction_id=transaction_id,
                    installment_number=index,
                    installments_total=installments_total,
                    principal_amount=principal,
                    interest_amount=Decimal("0.00"),
                    total_amount=principal,
                    scheduled_due_date=next_due,
                    scheduled_period=scheduled_date.strftime("%Y-%m"),
                    status="pending",
                    paid_amount=Decimal("0.00"),
                )
            )
        return installments

    async def _apply_payment_to_installments(
        self, user_id: uuid.UUID, card_id: uuid.UUID, amount: Decimal
    ) -> None:
        remaining = amount
        installments = await self.repo.list_pending_installments_for_update(card_id, user_id)
        for installment in installments:
            if remaining <= 0:
                break
            installment_remaining = installment.principal_amount - installment.paid_amount
            applied = min(remaining, installment_remaining)
            installment.paid_amount += applied
            remaining -= applied
            if installment.paid_amount >= installment.principal_amount:
                installment.status = "paid"
            elif installment.paid_amount > 0:
                installment.status = "partial"

    async def _enrich_card(self, card: CreditCard) -> CreditCardRead:
        status = await self._calculate_card_status(card)
        debt = status.current_debt
        monthly_pay = status.next_payment_estimate
        card_data = {
            key: value
            for key, value in card.__dict__.items()
            if not key.startswith("_") and key != "current_debt"
        }
        return CreditCardRead(
            **card_data,
            current_debt=debt,
            total_debt=debt,
            available_credit=status.available_credit,
            billed_debt=status.billed_debt,
            unbilled_debt=status.unbilled_debt,
            payment_required=status.payment_required,
            next_payment_estimate=status.next_payment_estimate,
            statement_balance=None,
            data_quality=status.data_quality,
            estimated_current_debt=debt,
            monthly_cc_payment=monthly_pay,
        )

    async def create_card(self, user_id: uuid.UUID, payload: CreditCardCreate) -> CreditCardRead:
        card = CreditCard(
            user_id=user_id,
            name=payload.name,
            bank=payload.bank,
            credit_limit=payload.credit_limit,
            cutoff_day=payload.cutoff_day,
            due_day=payload.due_day,
            management_fee=payload.management_fee,
            monthly_interest_rate=payload.monthly_interest_rate,
            annual_interest_rate=payload.annual_interest_rate,
            network=payload.network,
            franchise=payload.franchise,
            currency=payload.currency,
        )
        await self.repo.add(card)
        return await self._enrich_card(card)

    async def list_cards(self, user_id: uuid.UUID) -> list[CreditCardRead]:
        cards = await self.repo.get_all_for_user(user_id)
        enriched = []
        for c in cards:
            enriched.append(await self._enrich_card(c))
        return enriched

    async def get_card(self, user_id: uuid.UUID, card_id: uuid.UUID) -> CreditCardRead:
        card = await self.repo.get_by_id(card_id)
        if not card or card.user_id != user_id or not card.is_active:
            raise CreditCardNotFoundError()
        return await self._enrich_card(card)

    async def update_card(
        self, user_id: uuid.UUID, card_id: uuid.UUID, payload: CreditCardUpdate
    ) -> CreditCardRead:
        card = await self.repo.get_by_id_for_update(card_id)
        if not card or card.user_id != user_id or not card.is_active:
            raise CreditCardNotFoundError()

        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(card, key, value)

        await self.session.flush()
        return await self._enrich_card(card)

    async def delete_card(self, user_id: uuid.UUID, card_id: uuid.UUID) -> None:
        card = await self.repo.get_by_id_for_update(card_id)
        if not card or card.user_id != user_id or not card.is_active:
            raise CreditCardNotFoundError()
        card.is_active = False
        await self.session.flush()

    async def create_purchase(
        self,
        user_id: uuid.UUID,
        card_id: uuid.UUID,
        payload: CreditCardPurchaseCreate,
        command_id: uuid.UUID | str,
    ) -> CreditCardPurchaseResult:
        card = await self.repo.get_by_id_for_update(card_id)
        if not card or card.user_id != user_id:
            raise CreditCardNotFoundError()
        if not card.is_active:
            raise CreditCardInactiveError()

        debt, _ = await self.repo.get_card_debt(card.id)
        estimated_debt = Decimal(str(debt))
        estimated_available = max(Decimal("0.00"), card.credit_limit - estimated_debt)

        if payload.amount > estimated_available:
            raise CreditLimitExceededError()

        # Insert financial event (direction=neutral for purchase)
        event_payload = LedgerEventCreate(
            user_id=user_id,
            event_type="credit_card_purchase",
            direction="neutral",
            amount=payload.amount,
            currency=card.currency,
            category_id=payload.category_id,
            description=payload.description,
            source_message_id=payload.source_message_id,
            raw_message=payload.raw_message,
            metadata={"credit_card_id": str(card.id)},
        )

        event_payload.command_id = self._normalize_command_id(command_id)
        result = await self.ledger_service.record_event(event_payload)
        event, is_retry = result.event, result.idempotent

        if is_retry:
            transaction = await self.repo.get_transaction_by_event(event.id)
            return CreditCardPurchaseResult(
                status="idempotent_retry",
                event_id=event.id,
                transaction_id=transaction.id if transaction else None,
                amount=event.amount,
                current_debt=estimated_debt,
                available_credit=estimated_available,
                estimated_current_debt=estimated_debt,
                estimated_available_credit=estimated_available,
            )

        # Create the transaction
        monthly_amount = payload.amount / Decimal(payload.installments_total)
        transaction = CreditCardTransaction(
            user_id=user_id,
            credit_card_id=card.id,
            type="purchase",
            amount=payload.amount,
            category_id=payload.category_id,
            description=payload.description,
            installments_total=payload.installments_total,
            monthly_amount=monthly_amount,
            event_id=event.id,
        )
        await self.repo.add_transaction(transaction)

        installments = await self._build_installments(
            user_id=user_id,
            card=card,
            transaction_id=transaction.id,
            amount=payload.amount,
            installments_total=payload.installments_total,
        )
        await self.repo.add_installments(installments)

        new_debt = estimated_debt + payload.amount
        new_available = max(Decimal("0.00"), card.credit_limit - new_debt)

        return CreditCardPurchaseResult(
            status="success",
            event_id=event.id,
            transaction_id=transaction.id,
            amount=payload.amount,
            current_debt=new_debt,
            available_credit=new_available,
            estimated_current_debt=new_debt,
            estimated_available_credit=new_available,
        )

    async def create_payment(
        self,
        user_id: uuid.UUID,
        card_id: uuid.UUID,
        payload: CreditCardPaymentCreate,
        command_id: uuid.UUID | str,
    ) -> CreditCardPaymentResult:
        account = await self.account_repo.get_by_id_for_update(payload.account_id)
        if not account:
            from app.core.errors import NotFoundError

            raise NotFoundError(message="Cuenta no encontrada.")

        if account.user_id != user_id or not account.is_active:
            from app.accounts.exceptions import AccountForbiddenError

            raise AccountForbiddenError()

        if account.balance < payload.amount:
            from app.cash.exceptions import InsufficientFundsError

            raise InsufficientFundsError()

        card = await self.repo.get_by_id_for_update(card_id)
        if not card or card.user_id != user_id:
            raise CreditCardNotFoundError()
        if not card.is_active:
            raise CreditCardInactiveError()

        debt, _ = await self.repo.get_card_debt(card.id)
        estimated_debt = Decimal(str(debt))

        if payload.amount > estimated_debt:
            raise InvalidPaymentAmountError()

        # Create financial event (direction=outflow for payment)
        event_payload = LedgerEventCreate(
            user_id=user_id,
            event_type="credit_card_payment",
            direction="outflow",
            amount=payload.amount,
            currency=card.currency,
            account_id=payload.account_id,
            source_message_id=payload.source_message_id,
            raw_message=payload.raw_message,
            metadata={"credit_card_id": str(card.id)},
        )

        event_payload.command_id = self._normalize_command_id(command_id)
        result = await self.ledger_service.record_event(event_payload)
        event, is_retry = result.event, result.idempotent

        if is_retry:
            transaction = await self.repo.get_transaction_by_event(event.id)
            return CreditCardPaymentResult(
                status="idempotent_retry",
                event_id=event.id,
                transaction_id=transaction.id if transaction else None,
                amount=event.amount,
                current_debt=estimated_debt,
                available_credit=max(Decimal("0.00"), card.credit_limit - estimated_debt),
                estimated_current_debt=estimated_debt,
                account_balance=account.balance,
            )

        account.balance -= payload.amount

        transaction = CreditCardTransaction(
            user_id=user_id,
            credit_card_id=card.id,
            type="payment",
            amount=payload.amount,
            account_id=payload.account_id,
            event_id=event.id,
        )
        await self.repo.add_transaction(transaction)
        await self._apply_payment_to_installments(user_id, card.id, payload.amount)

        new_debt = estimated_debt - payload.amount
        new_available = max(Decimal("0.00"), card.credit_limit - new_debt)

        return CreditCardPaymentResult(
            status="success",
            event_id=event.id,
            transaction_id=transaction.id,
            amount=payload.amount,
            current_debt=new_debt,
            available_credit=new_available,
            estimated_current_debt=new_debt,
            account_balance=account.balance,
        )

    async def _calculate_card_status(self, card: CreditCard) -> CreditCardStatusRead:
        from datetime import date

        from app.credit.utils import calculate_credit_card_dates

        current_date = date.today()
        from datetime import timedelta

        cycle_start, cycle_end, next_due = calculate_credit_card_dates(
            current_date, card.cutoff_day, card.due_day
        )

        last_cutoff = cycle_start - timedelta(days=1)
        status_data = await self.repo.get_card_status_data(card.id, last_cutoff)
        next_payment_estimate = await self.repo.get_next_payment_estimate(card.id)

        billed_purchases = status_data["billed_purchases"]
        unbilled_purchases = status_data["unbilled_purchases"]
        total_payments = status_data["total_payments"]

        # Apply payments to billed purchases first
        billed_debt = billed_purchases - total_payments
        if billed_debt < 0:
            unbilled_debt = max(Decimal("0.00"), unbilled_purchases + billed_debt)
            billed_debt = Decimal("0.00")
        else:
            unbilled_debt = unbilled_purchases

        total_debt = billed_debt + unbilled_debt
        available_credit = max(Decimal("0.00"), card.credit_limit - total_debt)
        payment_required = billed_debt

        return CreditCardStatusRead(
            card_id=card.id,
            name=card.name,
            credit_limit=card.credit_limit,
            management_fee=card.management_fee or Decimal("0.00"),
            monthly_interest_rate=card.monthly_interest_rate or Decimal("0.00"),
            annual_interest_rate=card.annual_interest_rate or Decimal("0.00"),
            network=card.network,
            franchise=card.franchise,
            currency=card.currency,
            current_debt=total_debt,
            total_debt=total_debt,
            billed_debt=billed_debt,
            unbilled_debt=unbilled_debt,
            available_credit=available_credit,
            payment_required=payment_required,
            next_payment_estimate=next_payment_estimate,
            statement_balance=None,
            monthly_cc_payment=next_payment_estimate,
            cutoff_day=card.cutoff_day,
            payment_due_day=card.due_day,
            next_payment_due_date=next_due.isoformat(),
            purchases_count=status_data["purchases_count"],
            payments_count=status_data["payments_count"],
            data_quality=self._data_quality(),
        )

    async def get_card_status(
        self, user_id: uuid.UUID, card_id: uuid.UUID
    ) -> "CreditCardStatusRead":
        card = await self.repo.get_by_id(card_id)
        if not card or card.user_id != user_id or not card.is_active:
            raise CreditCardNotFoundError()

        return await self._calculate_card_status(card)

    async def get_credit_summary(self, user_id: uuid.UUID) -> "CreditSummaryRead":
        from app.credit.schemas import CreditSummaryRead

        cards = await self.repo.get_all_for_user(user_id)

        statuses = []
        for c in cards:
            statuses.append(await self.get_card_status(user_id, c.id))

        return CreditSummaryRead(
            total_credit_limit=sum((s.credit_limit for s in statuses), Decimal("0.00")),
            total_debt=sum((s.total_debt for s in statuses), Decimal("0.00")),
            total_available_credit=sum((s.available_credit for s in statuses), Decimal("0.00")),
            total_monthly_cc_payment=sum(
                (s.next_payment_estimate for s in statuses), Decimal("0.00")
            ),
            total_payment_required=sum((s.payment_required for s in statuses), Decimal("0.00")),
            total_next_payment_estimate=sum(
                (s.next_payment_estimate for s in statuses), Decimal("0.00")
            ),
            cards=statuses,
        )

    async def list_installments(
        self, user_id: uuid.UUID, card_id: uuid.UUID
    ) -> list[CreditCardInstallmentRead]:
        card = await self.repo.get_by_id(card_id)
        if not card or card.user_id != user_id or not card.is_active:
            raise CreditCardNotFoundError()
        installments = await self.repo.list_installments(card_id, user_id)
        return [CreditCardInstallmentRead.model_validate(i) for i in installments]
