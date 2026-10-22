import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.core.currency import round_to_minimum_unit
from app.core.errors import ValidationError
from app.core.financial_time import financial_date, financial_day_start
from app.core.logging import get_logger
from app.fx.service import FXService
from app.obligations.auto_repository import ObligationAutoPaymentRepository
from app.obligations.enums_v17 import PeriodStatus
from app.obligations.models import (
    Obligation,
    ObligationAutoPaymentConfig,
    ObligationAutoPaymentRun,
    ObligationPeriod,
)
from app.obligations.period_engine import PeriodEngine
from app.obligations.schemas_v17 import (
    ObligationAutoPaymentConfigRead,
    ObligationAutoPaymentConfigUpdateRequest,
    ObligationAutoPaymentRunRead,
    ObligationAutoPaymentRunsPage,
    ObligationPeriodPaymentCreateRequest,
)
from app.obligations.service_v17 import ObligationV17Service
from app.obligations.settlement import find_replay_by_command_id

logger = get_logger(__name__)


@dataclass
class AutoPaymentProcessorSummary:
    selected: int = 0
    succeeded: int = 0
    skipped: int = 0
    failed: int = 0
    technical_failures: int = 0


class ObligationAutoPaymentService:
    def __init__(
        self,
        session: AsyncSession,
        v17_service: ObligationV17Service,
        fx_service: FXService,
        account_repo: AccountRepository,
        repo: ObligationAutoPaymentRepository | None = None,
    ):
        self.session = session
        self.v17_service = v17_service
        self.fx_service = fx_service
        self.account_repo = account_repo
        self.repo = repo or ObligationAutoPaymentRepository(session)

    async def get_config(
        self, user_id: UUID, obligation_id: UUID
    ) -> ObligationAutoPaymentConfigRead:
        obligation = await self._get_obligation(obligation_id, user_id)
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        config = await self.repo.get_config(obligation_id, user_id)
        if not config:
            raise HTTPException(status_code=404, detail="auto_payment_config_not_found")

        return ObligationAutoPaymentConfigRead.model_validate(config)

    async def put_config(
        self,
        user_id: UUID,
        obligation_id: UUID,
        payload: ObligationAutoPaymentConfigUpdateRequest,
    ) -> ObligationAutoPaymentConfigRead:
        obligation = await self._get_obligation(obligation_id, user_id)
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        existing_config = await self.repo.get_config(obligation_id, user_id)
        now_utc = datetime.now(UTC)

        if not payload.enabled:
            # PUT must always allow disabling existing config even if Account became
            # inactive or parent terminal.
            if existing_config:
                existing_config.enabled = False
                existing_config.status = "paused"
                existing_config.pause_reason = "user_paused"
                existing_config.next_evaluation_at = None
                existing_config.updated_at = now_utc
                # Ignore payload.account_id on disable to prevent assigning foreign/unvalidated account
                await self.session.commit()
                return ObligationAutoPaymentConfigRead.model_validate(existing_config)

            # Creating in disabled state requires an account_id owned by the user
            if not payload.account_id:
                raise HTTPException(status_code=422, detail="account_id_required")

            account = await self.account_repo.get_by_id(payload.account_id, include_inactive=True)
            if not account or account.user_id != user_id:
                raise HTTPException(status_code=404, detail="account_not_found")
            if not account.is_active:
                raise HTTPException(status_code=400, detail="account_inactive")

            new_config = ObligationAutoPaymentConfig(
                id=uuid.uuid4(),
                obligation_id=obligation.id,
                user_id=user_id,
                account_id=payload.account_id,
                enabled=False,
                status="paused",
                pause_reason="user_paused",
                next_evaluation_at=None,
                created_at=now_utc,
                updated_at=now_utc,
            )
            await self.repo.create_config(new_config)
            await self.session.commit()
            return ObligationAutoPaymentConfigRead.model_validate(new_config)

        # Enabling: validate own active Account and active parent
        if obligation.status != "active":
            raise HTTPException(status_code=400, detail="obligation_inactive")

        target_account_id = payload.account_id or (
            existing_config.account_id if existing_config else None
        )
        if not target_account_id:
            raise HTTPException(status_code=422, detail="account_id_required")

        account = await self.account_repo.get_by_id(target_account_id, include_inactive=True)
        if not account or account.user_id != user_id:
            raise HTTPException(status_code=404, detail="account_not_found")
        if not account.is_active:
            raise HTTPException(status_code=400, detail="account_inactive")

        # Determine next evaluation
        canonical_today = financial_date(now_utc)
        due_period = await self.repo.get_oldest_due_open_period(obligation.id, canonical_today)
        if due_period:
            next_eval = now_utc
        else:
            next_eval = financial_day_start(canonical_today + timedelta(days=1))

        if existing_config:
            existing_config.account_id = target_account_id
            existing_config.enabled = True
            existing_config.status = "enabled"
            existing_config.pause_reason = None
            existing_config.next_evaluation_at = next_eval
            existing_config.updated_at = now_utc
            await self.session.commit()
            return ObligationAutoPaymentConfigRead.model_validate(existing_config)

        new_config = ObligationAutoPaymentConfig(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            user_id=user_id,
            account_id=target_account_id,
            enabled=True,
            status="enabled",
            pause_reason=None,
            next_evaluation_at=next_eval,
            created_at=now_utc,
            updated_at=now_utc,
        )
        await self.repo.create_config(new_config)
        await self.session.commit()
        return ObligationAutoPaymentConfigRead.model_validate(new_config)

    async def list_runs(
        self, user_id: UUID, obligation_id: UUID, limit: int = 50, offset: int = 0
    ) -> ObligationAutoPaymentRunsPage:
        obligation = await self._get_obligation(obligation_id, user_id)
        if not obligation:
            raise HTTPException(status_code=404, detail="obligation_not_found")

        runs, total = await self.repo.list_runs_paginated(obligation_id, limit=limit, offset=offset)
        return ObligationAutoPaymentRunsPage(
            items=[ObligationAutoPaymentRunRead.model_validate(r) for r in runs],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def process_due_auto_payments(
        self,
        as_of: datetime | None = None,
        batch_size: int = 50,
        session_factory: Any | None = None,
    ) -> AutoPaymentProcessorSummary:
        summary = AutoPaymentProcessorSummary()
        now_utc = as_of or datetime.now(UTC)
        canonical_today = financial_date(now_utc)
        next_canonical_day = financial_day_start(canonical_today + timedelta(days=1))

        attempted_config_ids: set[UUID] = set()

        for _ in range(batch_size):
            config = await self.repo.get_next_eligible_config_for_update(
                as_of=now_utc,
                excluded_config_ids=list(attempted_config_ids) if attempted_config_ids else None,
            )
            if not config:
                break

            attempted_config_ids.add(config.id)
            summary.selected += 1
            try:
                await self._process_single_config(
                    config=config,
                    canonical_today=canonical_today,
                    now_utc=now_utc,
                    next_canonical_day=next_canonical_day,
                    summary=summary,
                )
            except Exception as exc:
                cfg_id = config.id
                obl_id = config.obligation_id
                logger.error(
                    "auto_payment_config_processing_failed",
                    config_id=str(cfg_id),
                    obligation_id=str(obl_id),
                    error_type=type(exc).__name__,
                )
                summary.technical_failures += 1
                summary.failed += 1
                # Rollback financial changes immediately
                await self.session.rollback()
                # Persist technical failure in fresh session
                await self._persist_technical_failure(
                    config=config,
                    canonical_today=canonical_today,
                    now_utc=now_utc,
                    next_canonical_day=next_canonical_day,
                    session_factory=session_factory,
                    config_id=cfg_id,
                    obligation_id=obl_id,
                )

        return summary

    async def _process_single_config(
        self,
        config: ObligationAutoPaymentConfig,
        canonical_today: date,
        now_utc: datetime,
        next_canonical_day: datetime,
        summary: AutoPaymentProcessorSummary,
    ) -> None:
        if not config.enabled or config.status != "enabled":
            return

        # 1. Lock root Obligation
        stmt_obl = (
            select(Obligation)
            .where(
                Obligation.id == config.obligation_id,
                Obligation.user_id == config.user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        obligation = (await self.session.execute(stmt_obl)).scalars().first()
        if not obligation:
            config.enabled = False
            config.status = "paused"
            config.pause_reason = "obligation_not_found"
            await self.session.commit()
            summary.skipped += 1
            return

        if obligation.status != "active":
            config.enabled = False
            config.status = "paused"
            config.pause_reason = (
                "obligation_completed"
                if obligation.status in ("completed", "paid")
                else "obligation_inactive"
            )
            config.next_evaluation_at = None

            attempt_key = f"{config.id}:none:{canonical_today.isoformat()}"
            run_id = uuid.uuid5(uuid.NAMESPACE_OID, f"auto_run:{attempt_key}")
            run = ObligationAutoPaymentRun(
                id=run_id,
                config_id=config.id,
                obligation_id=obligation.id,
                obligation_period_id=None,
                attempt_key=attempt_key,
                canonical_date=canonical_today,
                status="skipped",
                result_code="obligation_inactive",
                executed_at=now_utc,
            )
            self.session.add(run)
            await self.session.commit()
            summary.skipped += 1
            return

        # Advance the contractual lifecycle even when no client has listed periods.
        await PeriodEngine(self.session).sync_periods(obligation, canonical_today)

        # 2. Find oldest due open period
        period = await self.repo.get_oldest_due_open_period(obligation.id, canonical_today)
        if not period:
            config.next_evaluation_at = next_canonical_day
            attempt_key = f"{config.id}:none:{canonical_today.isoformat()}"
            run_id = uuid.uuid5(uuid.NAMESPACE_OID, f"auto_run:{attempt_key}")
            run = ObligationAutoPaymentRun(
                id=run_id,
                config_id=config.id,
                obligation_id=obligation.id,
                obligation_period_id=None,
                attempt_key=attempt_key,
                canonical_date=canonical_today,
                status="skipped",
                result_code="no_due_periods",
                executed_at=now_utc,
            )
            self.session.add(run)
            await self.session.commit()
            summary.skipped += 1
            return

        # 3. Lock period under lock
        stmt_p = (
            select(ObligationPeriod)
            .where(
                ObligationPeriod.obligation_id == obligation.id,
                ObligationPeriod.id == period.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        period = (await self.session.execute(stmt_p)).scalars().first()
        if not period:
            config.next_evaluation_at = next_canonical_day
            await self.session.commit()
            summary.skipped += 1
            return

        attempt_key = f"{config.id}:{period.id}:{canonical_today.isoformat()}"
        run_id = uuid.uuid5(uuid.NAMESPACE_OID, f"auto_run:{attempt_key}")

        # 4. Attempt key deduplication check
        existing_run = await self.repo.get_run_by_attempt_key(attempt_key)
        if existing_run and existing_run.status in ("succeeded", "skipped", "failed"):
            if config.next_evaluation_at and config.next_evaluation_at <= now_utc:
                config.next_evaluation_at = next_canonical_day
                await self.session.commit()
            summary.skipped += 1
            return

        # 5. Check if period already paid/cancelled/skipped
        if period.status in ("paid", "cancelled", "skipped") or (
            period.amount is not None and period.paid_amount >= period.amount
        ):
            config.next_evaluation_at = next_canonical_day
            run = ObligationAutoPaymentRun(
                id=run_id,
                config_id=config.id,
                obligation_id=obligation.id,
                obligation_period_id=period.id,
                attempt_key=attempt_key,
                canonical_date=canonical_today,
                status="skipped",
                result_code="already_paid",
                executed_at=now_utc,
            )
            self.session.add(run)
            await self.session.commit()
            summary.skipped += 1
            return

        # 6. Check if period requires amount definition
        if period.status == PeriodStatus.pending_amount_definition.value or period.amount is None:
            config.next_evaluation_at = next_canonical_day
            run = ObligationAutoPaymentRun(
                id=run_id,
                config_id=config.id,
                obligation_id=obligation.id,
                obligation_period_id=period.id,
                attempt_key=attempt_key,
                canonical_date=canonical_today,
                status="skipped",
                result_code="period_amount_not_defined",
                executed_at=now_utc,
            )
            self.session.add(run)
            await self.session.commit()
            summary.skipped += 1
            return

        remaining_amount = period.amount - period.paid_amount
        if remaining_amount <= Decimal("0"):
            config.next_evaluation_at = next_canonical_day
            await self.session.commit()
            summary.skipped += 1
            return

        # 7. Early exact replay check using deterministic command identity
        command_id = uuid.uuid5(uuid.NAMESPACE_OID, f"auto_pay:{obligation.id}:{period.id}")
        existing_event, existing_payments = await find_replay_by_command_id(
            self.session, config.user_id, command_id
        )
        if existing_event and existing_payments:
            run = ObligationAutoPaymentRun(
                id=run_id,
                config_id=config.id,
                obligation_id=obligation.id,
                obligation_period_id=period.id,
                attempt_key=attempt_key,
                canonical_date=canonical_today,
                status="succeeded",
                result_code="success",
                amount=existing_payments[0].amount,
                source_amount=existing_payments[0].source_amount,
                currency=existing_payments[0].currency,
                source_currency=existing_payments[0].source_currency,
                fx_rate=existing_payments[0].fx_rate,
                payment_id=existing_payments[0].id,
                executed_at=now_utc,
            )
            self.session.add(run)
            config.next_evaluation_at = next_canonical_day
            await self.session.commit()
            summary.succeeded += 1
            return

        # 8. Lock own source Account
        account = await self.account_repo.get_by_id_for_update(
            config.account_id, include_inactive=True
        )
        if not account or account.user_id != config.user_id:
            config.enabled = False
            config.status = "paused"
            config.pause_reason = "account_not_found"
            config.next_evaluation_at = None
            run = ObligationAutoPaymentRun(
                id=run_id,
                config_id=config.id,
                obligation_id=obligation.id,
                obligation_period_id=period.id,
                attempt_key=attempt_key,
                canonical_date=canonical_today,
                status="failed",
                result_code="account_not_found",
                executed_at=now_utc,
            )
            self.session.add(run)
            await self.session.commit()
            summary.failed += 1
            return

        if not account.is_active:
            config.enabled = False
            config.status = "paused"
            config.pause_reason = "account_inactive"
            config.next_evaluation_at = None
            run = ObligationAutoPaymentRun(
                id=run_id,
                config_id=config.id,
                obligation_id=obligation.id,
                obligation_period_id=period.id,
                attempt_key=attempt_key,
                canonical_date=canonical_today,
                status="failed",
                result_code="account_inactive",
                executed_at=now_utc,
            )
            self.session.add(run)
            await self.session.commit()
            summary.failed += 1
            return

        if (
            account.balance is None
            or not account.balance.is_finite()
            or account.balance < Decimal("0")
        ):
            config.next_evaluation_at = next_canonical_day
            run = ObligationAutoPaymentRun(
                id=run_id,
                config_id=config.id,
                obligation_id=obligation.id,
                obligation_period_id=period.id,
                attempt_key=attempt_key,
                canonical_date=canonical_today,
                status="failed",
                result_code="account_unavailable",
                executed_at=now_utc,
            )
            self.session.add(run)
            await self.session.commit()
            summary.failed += 1
            return

        # 9. FX Rate Snapshot (flush-only inside UoW)
        rate_snapshot_id = None
        fx_rate = Decimal("1.00000000")
        if account.currency.upper() != obligation.currency.upper():
            try:
                snapshot = await self.fx_service.get_rate_snapshot(
                    account.currency, obligation.currency, commit=False
                )
                rate_snapshot_id = snapshot.id
                fx_rate = snapshot.rate
                if not fx_rate.is_finite() or fx_rate <= Decimal("0"):
                    raise ValidationError(
                        message="Invalid FX rate", error_code="fx_rate_unavailable"
                    )
            except ValidationError:
                config.next_evaluation_at = next_canonical_day
                run = ObligationAutoPaymentRun(
                    id=run_id,
                    config_id=config.id,
                    obligation_id=obligation.id,
                    obligation_period_id=period.id,
                    attempt_key=attempt_key,
                    canonical_date=canonical_today,
                    status="failed",
                    result_code="fx_rate_unavailable",
                    error_message="FX rate unavailable from provider",
                    executed_at=now_utc,
                )
                self.session.add(run)
                await self.session.commit()
                summary.failed += 1
                return

        # 10. Availability Check
        if account.currency.upper() == obligation.currency.upper():
            source_amount_needed = remaining_amount
        else:
            source_amount_needed = round_to_minimum_unit(
                remaining_amount / fx_rate, account.currency
            )

        from app.goals.repository import GoalRepository

        goal_repo = GoalRepository(self.session)
        reserved = await goal_repo.calculate_reserved_by_account(account.id, config.user_id)
        available_balance = account.balance - reserved

        if available_balance < source_amount_needed:
            config.next_evaluation_at = next_canonical_day
            run = ObligationAutoPaymentRun(
                id=run_id,
                config_id=config.id,
                obligation_id=obligation.id,
                obligation_period_id=period.id,
                attempt_key=attempt_key,
                canonical_date=canonical_today,
                status="skipped",
                result_code="insufficient_balance",
                amount=remaining_amount,
                source_amount=source_amount_needed,
                currency=obligation.currency,
                source_currency=account.currency,
                executed_at=now_utc,
            )
            self.session.add(run)
            await self.session.commit()
            summary.skipped += 1
            return

        # 11. Execute Payment via Canonical Specific Primitive
        payment_request = ObligationPeriodPaymentCreateRequest(
            amount=remaining_amount,
            currency=obligation.currency,
            source_account_id=account.id,
            source_currency=account.currency,
            rate_snapshot_id=rate_snapshot_id,
        )
        payment, _ = await self.v17_service.pay_specific_period(
            user_id=config.user_id,
            obligation_id=obligation.id,
            period_id=period.id,
            data=payment_request,
            channel="automatic",
            override_command_id=command_id,
        )

        # 12. Create Succeeded Run and Advance Config
        run = ObligationAutoPaymentRun(
            id=run_id,
            config_id=config.id,
            obligation_id=obligation.id,
            obligation_period_id=period.id,
            attempt_key=attempt_key,
            canonical_date=canonical_today,
            status="succeeded",
            result_code="success",
            amount=payment.amount,
            source_amount=payment.source_amount,
            currency=payment.currency,
            source_currency=payment.source_currency,
            fx_rate=payment.fx_rate,
            payment_id=payment.id,
            executed_at=now_utc,
        )
        self.session.add(run)

        config.next_evaluation_at = next_canonical_day
        await self.session.commit()
        summary.succeeded += 1

    async def _persist_technical_failure(
        self,
        config: ObligationAutoPaymentConfig,
        canonical_today: date,
        now_utc: datetime,
        next_canonical_day: datetime,
        session_factory: Any | None,
        period_id: UUID | None = None,
        *,
        config_id: UUID | None = None,
        obligation_id: UUID | None = None,
    ) -> None:
        target_config_id = config_id if config_id is not None else config.id
        target_obligation_id = obligation_id if obligation_id is not None else config.obligation_id
        attempt_key = f"{target_config_id}:{period_id or 'none'}:{canonical_today.isoformat()}"
        run_id = uuid.uuid5(uuid.NAMESPACE_OID, f"auto_run:{attempt_key}")

        session_ctx = None
        fresh_session = None
        try:
            if session_factory:
                session_ctx = session_factory()
                fresh_session = await session_ctx.__aenter__()
            else:
                fresh_session = self.session

            # 1. Lock config in fresh transaction
            cfg_stmt = (
                select(ObligationAutoPaymentConfig)
                .where(ObligationAutoPaymentConfig.id == target_config_id)
                .with_for_update()
            )
            fresh_config = (await fresh_session.execute(cfg_stmt)).scalars().first()
            if not fresh_config:
                return

            # 2. Check for concurrent winner or existing run for this attempt
            winner_stmt = select(ObligationAutoPaymentRun).where(
                ObligationAutoPaymentRun.config_id == target_config_id,
                ObligationAutoPaymentRun.canonical_date == canonical_today,
            )
            existing_runs = (await fresh_session.execute(winner_stmt)).scalars().all()
            for r in existing_runs:
                if r.status == "succeeded" or r.attempt_key == attempt_key:
                    # Winner already succeeded or attempt already recorded
                    return

            fresh_config.next_evaluation_at = next_canonical_day

            failed_run = ObligationAutoPaymentRun(
                id=run_id,
                config_id=target_config_id,
                obligation_id=target_obligation_id,
                obligation_period_id=period_id,
                attempt_key=attempt_key,
                canonical_date=canonical_today,
                status="failed",
                result_code="technical_error",
                error_message="Technical error during execution",
                executed_at=now_utc,
            )
            fresh_session.add(failed_run)
            await fresh_session.commit()
        except Exception as exc:
            logger.error(
                "auto_payment_technical_failure_persistence_failed",
                config_id=str(config.id),
                error_type=type(exc).__name__,
            )
            if fresh_session is not None:
                try:
                    await fresh_session.rollback()
                except Exception as rollback_exc:
                    logger.error(
                        "auto_payment_technical_failure_rollback_failed",
                        config_id=str(config.id),
                        error_type=type(rollback_exc).__name__,
                    )
            raise RuntimeError("Failed to persist technical failure") from exc
        finally:
            if session_ctx is not None:
                await session_ctx.__aexit__(None, None, None)

    async def _get_obligation(self, obligation_id: UUID, user_id: UUID) -> Obligation | None:
        stmt = select(Obligation).where(
            Obligation.id == obligation_id, Obligation.user_id == user_id
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()
