import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.core.errors import (
    ValidationError,
)
from app.fx.provider import FxRateProvider
from app.fx.schemas import FXQuoteResponse, FXRatesLatestResponse

logger = logging.getLogger(__name__)


class FXService:
    def __init__(self, provider: FxRateProvider):
        self.provider = provider
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

    async def create_quote(
        self,
        user_id: UUID,
        source_currency: str,
        target_currency: str,
        source_amount: Decimal,
        idempotency_key: str | None = None,
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

        # TODO: Persist FXQuote to DB if models are available. For Phase 5B, returning mock-persistence is enough as we don't modify the database.
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
