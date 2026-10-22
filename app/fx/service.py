import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.fx.provider import FxRateProvider
from app.fx.schemas import FXQuoteResponse, FXRatesLatestResponse, FXRateSnapshotResponse
from app.obligations.models import ExchangeRate, FXQuote

logger = logging.getLogger(__name__)


class FXService:
    def __init__(self, provider: FxRateProvider, session: AsyncSession | None = None):
        self.provider = provider
        self.session = session
        self.quote_validity_minutes = 5
        self.tolerance_bps = 50

    async def get_latest_rate(
        self, base_currency: str, quote_currency: str
    ) -> FXRatesLatestResponse:
        base_c = base_currency.upper()
        quote_c = quote_currency.upper()

        rate = await self.provider.get_rate(base_c, quote_c)
        if rate is None:
            raise ValidationError(
                message="FX rate unavailable from provider.", error_code="fx_rate_unavailable"
            )

        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=self.quote_validity_minutes)

        return FXRatesLatestResponse(
            base_currency=base_c,
            rates={quote_c: rate},
            fetched_at=now,
            expires_at=expires_at,
            provider=self.provider.__class__.__name__,
            is_stale=False,
        )

    async def get_rate_snapshot(
        self, base_currency: str, quote_currency: str, commit: bool = True
    ) -> FXRateSnapshotResponse:
        base_c = base_currency.upper()
        quote_c = quote_currency.upper()

        if base_c == quote_c:
            rate = Decimal("1.00000000")
        else:
            rate = await self.provider.get_rate(base_c, quote_c)
            if rate is None:
                raise ValidationError(
                    message="FX rate unavailable from provider.", error_code="fx_rate_unavailable"
                )

        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=self.quote_validity_minutes)
        snapshot_id = uuid.uuid4()

        if self.session:
            db_rate = ExchangeRate(
                id=snapshot_id,
                base_currency=base_c,
                quote_currency=quote_c,
                rate=rate,
                provider=self.provider.__class__.__name__,
                fetched_at=now,
                expires_at=expires_at,
                is_stale=False,
            )
            self.session.add(db_rate)
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()

        return FXRateSnapshotResponse(
            id=snapshot_id,
            from_currency=base_c,
            to_currency=quote_c,
            rate=rate,
            retrieved_at=now,
            expires_at=expires_at,
            source=self.provider.__class__.__name__,
            stale=False,
        )

    async def create_quote(
        self,
        user_id: UUID,
        source_currency: str,
        target_currency: str,
        source_amount: Decimal,
        idempotency_key: str | None = None,
        commit: bool = True,
    ) -> FXQuoteResponse:
        from_c = source_currency.upper()
        to_c = target_currency.upper()

        if source_amount <= Decimal("0"):
            raise ValidationError(
                message="Source amount must be greater than zero.", error_code="invalid_amount"
            )

        if from_c == to_c:
            # Same currency quote allowed, rate=1, target_amount=source_amount
            rate = Decimal("1.00000000")
            target_amount = source_amount
        else:
            rate = await self.provider.get_rate(from_c, to_c)
            if rate is None:
                raise ValidationError(
                    message="FX rate unavailable from provider.", error_code="fx_rate_unavailable"
                )

            # Calculation using Decimal
            target_amount = (source_amount * rate).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP)

        quote_id = uuid.uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=self.quote_validity_minutes)

        if self.session:
            db_quote = FXQuote(
                id=quote_id,
                user_id=user_id,
                from_currency=from_c,
                to_currency=to_c,
                source_amount=source_amount,
                target_amount=target_amount,
                rate=rate,
                provider=self.provider.__class__.__name__,
                rate_timestamp=now,
                expires_at=expires_at,
                status="active",
                idempotency_key=idempotency_key,
            )
            self.session.add(db_quote)
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()

        return FXQuoteResponse(
            quote_id=quote_id,
            source_amount=source_amount,
            target_amount=target_amount,
            from_currency=from_c,
            to_currency=to_c,
            rate=rate,
            expires_at=expires_at,
            tolerance_bps=self.tolerance_bps,
            status="active",
        )

    async def create_quote_for_target(
        self,
        user_id: UUID,
        source_currency: str,
        target_currency: str,
        target_amount: Decimal,
        idempotency_key: str | None = None,
        commit: bool = True,
    ) -> FXQuoteResponse:
        from_c = source_currency.upper()
        to_c = target_currency.upper()

        if (
            target_amount is None
            or not target_amount.is_finite()
            or target_amount <= Decimal("0")
            or target_amount > Decimal("9999999999999.99")
        ):
            raise ValidationError(
                message="Target amount must be a finite positive number not exceeding 9999999999999.99.",
                error_code="invalid_amount",
            )
        if target_amount.quantize(Decimal("0.01")) != target_amount:
            raise ValidationError(
                message="Target amount supports at most 2 decimal places.",
                error_code="invalid_amount_precision",
            )

        if from_c == to_c:
            rate = Decimal("1.00000000")
            source_amount = target_amount
        else:
            rate = await self.provider.get_rate(from_c, to_c)
            if rate is None or not rate.is_finite() or rate <= Decimal("0"):
                raise ValidationError(
                    message="FX rate unavailable from provider.", error_code="fx_rate_unavailable"
                )
            from app.core.currency import round_to_minimum_unit

            source_amount = round_to_minimum_unit(target_amount / rate, from_c)
            if not source_amount.is_finite() or source_amount <= Decimal("0"):
                raise ValidationError(
                    message="Calculated source amount is below minimum supported currency unit.",
                    error_code="below_minimum_source",
                )
            if source_amount > Decimal("9999999999999.99"):
                raise ValidationError(
                    message="Calculated source amount exceeds maximum supported amount.",
                    error_code="amount_overflow",
                )

        quote_id = uuid.uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=self.quote_validity_minutes)

        if self.session:
            db_quote = FXQuote(
                id=quote_id,
                user_id=user_id,
                from_currency=from_c,
                to_currency=to_c,
                source_amount=source_amount,
                target_amount=target_amount,
                rate=rate,
                provider=self.provider.__class__.__name__,
                rate_timestamp=now,
                expires_at=expires_at,
                status="active",
                idempotency_key=idempotency_key,
            )
            self.session.add(db_quote)
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()

        return FXQuoteResponse(
            quote_id=quote_id,
            source_amount=source_amount,
            target_amount=target_amount,
            from_currency=from_c,
            to_currency=to_c,
            rate=rate,
            expires_at=expires_at,
            tolerance_bps=self.tolerance_bps,
            status="active",
        )
