from decimal import Decimal

from app.core.currency import get_minimum_unit, round_to_minimum_unit


def test_get_minimum_unit():
    assert get_minimum_unit("COP") == Decimal("50.00")
    assert get_minimum_unit("USD") == Decimal("0.01")
    assert get_minimum_unit("EUR") == Decimal("0.01")
    assert get_minimum_unit("UNKNOWN") == Decimal("0.01")
    assert get_minimum_unit("cop") == Decimal("50.00")


def test_round_to_minimum_unit_cop():
    # Nearest 50 COP
    assert round_to_minimum_unit(Decimal("100"), "COP") == Decimal("100.00")
    assert round_to_minimum_unit(Decimal("124"), "COP") == Decimal("100.00")
    assert round_to_minimum_unit(Decimal("125"), "COP") == Decimal("150.00")
    assert round_to_minimum_unit(Decimal("126"), "COP") == Decimal("150.00")


def test_round_to_minimum_unit_usd():
    # Nearest 0.01 USD
    assert round_to_minimum_unit(Decimal("100.004"), "USD") == Decimal("100.00")
    assert round_to_minimum_unit(Decimal("100.005"), "USD") == Decimal("100.01")
    assert round_to_minimum_unit(Decimal("100.006"), "USD") == Decimal("100.01")
