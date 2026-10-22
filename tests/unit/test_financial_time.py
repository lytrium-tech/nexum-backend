from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.core.financial_time import financial_date, financial_day_start


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(2026, 10, 22, 4, 59, 59, tzinfo=UTC), date(2026, 10, 21)),
        (datetime(2026, 10, 22, 5, tzinfo=UTC), date(2026, 10, 22)),
        (datetime(2026, 10, 22, 0, 30, tzinfo=timezone(timedelta(hours=9))), date(2026, 10, 21)),
    ],
)
def test_calendar_uses_global_financial_day(instant, expected):
    assert financial_date(instant) == expected


def test_financial_day_start_is_persistable_utc():
    assert financial_day_start(date(2026, 10, 22)) == datetime(2026, 10, 22, 5, tzinfo=UTC)


def test_naive_clock_is_rejected():
    with pytest.raises(ValueError, match="aware"):
        financial_date(datetime(2026, 10, 22))
