"""Financial calendar shared with the versioned Ledger SQL; instants stay UTC."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

FINANCIAL_TIMEZONE = ZoneInfo("America/Bogota")


def financial_date(now: datetime | None = None) -> date:
    instant = now if now is not None else datetime.now(UTC)
    if instant.utcoffset() is None:
        raise ValueError("Financial clock requires an aware datetime")
    return instant.astimezone(FINANCIAL_TIMEZONE).date()


def financial_day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=FINANCIAL_TIMEZONE).astimezone(UTC)
