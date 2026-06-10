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
    ObligationAmountMismatchError,
    ObligationForbiddenError,
    ObligationInactiveError,
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

    async def list_obligations(self, auth_user_id: UUID) -> list[ObligationRead]:
        obligations = await self.repository.list_active(auth_user_id)
        return [ObligationRead.model_validate(o) for o in obligations]

    async def get_obligation(self, auth_user_id: UUID, obligation_id: UUID) -> ObligationRead:
        obligation = await self._get_obligation_or_404(obligation_id)
        if obligation.user_id != auth_user_id:
            raise ObligationForbiddenError()
        return ObligationRead.model_validate(obligation)

    async def create_obligation(
        self, auth_user_id: UUID, payload: ObligationCreate
    ) -> ObligationRead:
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
            due_day=payload.due_day,
            frequency=payload.frequency,
            category_id=payload.category_id,
            is_active=True,
            metadata_=payload.metadata,
        )
        created = await self.repository.create(db_obligation)
        await self.repository.session.refresh(created)
        return ObligationRead.model_validate(created)

    async def update_obligation(
        self, auth_user_id: UUID, obligation_id: UUID, payload: ObligationUpdate
    ) -> ObligationRead:
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
        if payload.due_day is not None:
            obligation.due_day = payload.due_day
        if payload.frequency is not None:
            obligation.frequency = payload.frequency
        if payload.category_id is not None:
            obligation.category_id = payload.category_id
        if payload.metadata is not None:
            obligation.metadata_ = payload.metadata

        await self.repository.session.flush()
        await self.repository.session.refresh(obligation)
        return ObligationRead.model_validate(obligation)

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
        obligation = await self.repository.get_by_id_for_update(obligation_id)
        if not obligation:
            raise NotFoundError(message="Obligación no encontrada.")

        if obligation.user_id != auth_user_id:
            raise ObligationForbiddenError()

        if not obligation.is_active:
            raise ObligationInactiveError()

        if payload.amount != obligation.amount:
            raise ObligationAmountMismatchError(str(obligation.amount))

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
