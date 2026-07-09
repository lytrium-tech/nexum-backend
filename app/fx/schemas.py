from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class FXRatesLatestResponse(BaseModel):
    base_currency: str = Field(min_length=3, max_length=3)
    rates: dict[str, Decimal]
    fetched_at: datetime
    expires_at: datetime
    provider: str
    is_stale: bool


class FXQuoteRequest(BaseModel):
    amount: Decimal = Field(gt=0, description="Source amount to quote")
    from_currency: str = Field(min_length=3, max_length=3)
    to_currency: str = Field(min_length=3, max_length=3)
    idempotency_key: str | None = None


class FXQuoteResponse(BaseModel):
    quote_id: UUID
    source_amount: Decimal
    target_amount: Decimal
    from_currency: str
    to_currency: str
    rate: Decimal
    expires_at: datetime
    tolerance_bps: int = 50
    status: str = "active"


class FXRateSnapshotResponse(BaseModel):
    id: str | UUID
    from_currency: str
    to_currency: str
    rate: Decimal | str
    retrieved_at: datetime
    expires_at: datetime
    source: str
    stale: bool
