import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.credit.exceptions import (
    CreditCardInactiveError,
    CreditCardNotFoundError,
    CreditLimitExceededError,
    InvalidPaymentAmountError,
)
from app.credit.models import CreditCard, CreditCardTransaction
from app.credit.repository import CreditCardRepository
from app.credit.schemas import (
    CreditCardCreate,
    CreditCardPaymentCreate,
    CreditCardPaymentResult,
    CreditCardPurchaseCreate,
    CreditCardPurchaseResult,
    CreditCardRead,
    CreditCardUpdate,
)
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate
from app.ledger.service import LedgerService


class CreditCardService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = CreditCardRepository(session)
        self.ledger_service = LedgerService(LedgerRepository(session))
        self.account_repo = AccountRepository(session)

    async def _enrich_card(self, card: CreditCard) -> CreditCardRead:
        debt, monthly_pay = await self.repo.get_card_debt(card.id)
        return CreditCardRead(
            **card.__dict__,
            estimated_current_debt=Decimal(str(debt)),
            monthly_cc_payment=Decimal(str(monthly_pay)),
        )

    async def create_card(self, user_id: uuid.UUID, payload: CreditCardCreate) -> CreditCardRead:
        card = CreditCard(
            user_id=user_id,
            name=payload.name,
            bank=payload.bank,
            credit_limit=payload.credit_limit,
            cutoff_day=payload.cutoff_day,
            due_day=payload.due_day,
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
        command_id: uuid.UUID,
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

        event_payload.command_id = command_id
        result = await self.ledger_service.record_event(event_payload)
        event, is_retry = result.event, result.idempotent

        if is_retry:
            transaction = await self.repo.get_transaction_by_event(event.id)
            return CreditCardPurchaseResult(
                status="idempotent_retry",
                event_id=event.id,
                transaction_id=transaction.id if transaction else None,
                amount=event.amount,
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

        new_debt = estimated_debt + payload.amount
        new_available = max(Decimal("0.00"), card.credit_limit - new_debt)

        return CreditCardPurchaseResult(
            status="success",
            event_id=event.id,
            transaction_id=transaction.id,
            amount=payload.amount,
            estimated_current_debt=new_debt,
            estimated_available_credit=new_available,
        )

    async def create_payment(
        self,
        user_id: uuid.UUID,
        card_id: uuid.UUID,
        payload: CreditCardPaymentCreate,
        command_id: uuid.UUID,
    ) -> CreditCardPaymentResult:
        account = await self.account_repo.get_by_id_for_update(payload.account_id)
        if not account or account.user_id != user_id or not account.is_active:
            raise ValueError("Account not found or inactive")
        if account.balance < payload.amount:
            raise ValueError("Insufficient balance")

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

        event_payload.command_id = command_id
        result = await self.ledger_service.record_event(event_payload)
        event, is_retry = result.event, result.idempotent

        if is_retry:
            transaction = await self.repo.get_transaction_by_event(event.id)
            return CreditCardPaymentResult(
                status="idempotent_retry",
                event_id=event.id,
                transaction_id=transaction.id if transaction else None,
                amount=event.amount,
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

        new_debt = estimated_debt - payload.amount

        return CreditCardPaymentResult(
            status="success",
            event_id=event.id,
            transaction_id=transaction.id,
            amount=payload.amount,
            estimated_current_debt=new_debt,
            account_balance=account.balance,
        )
