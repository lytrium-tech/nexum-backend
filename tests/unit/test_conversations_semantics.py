from app.conversations.intent_catalog import is_read_intent
from app.conversations.prompts import build_system_prompt, render_read_response


def test_ask_balance_uses_backend_data():
    data = {"total_available_real": "50000.00"}
    res = render_read_response("ask_balance", data)
    assert "50000.00" in res
    assert "real" in res


def test_ask_free_money_uses_backend_data_and_does_not_invent():
    data = {"free_money": "15000.00"}
    res = render_read_response("ask_free_money", data)
    assert "15000.00" in res


def test_ask_debt_uses_backend_data():
    data = {
        "total_estimated_credit_card_debt": "3000.00",
        "credit_cards": [{"credit_card_name": "Visa", "current_debt": "3000.00"}],
    }
    res = render_read_response("ask_debt", data)
    assert "3000.00" in res
    assert "Visa" in res


def test_ask_advice_has_disclaimer():
    res = render_read_response("ask_advice", {})
    assert "no reemplaza asesoría financiera profesional" in res


def test_system_prompt_enforces_no_calculation_and_no_currency_mixing():
    prompt = build_system_prompt([], [], [], [], [])
    assert "No calcules saldos ni operaciones matemáticas" in prompt
    assert "No mezcles monedas" in prompt
    assert "No calcules dinero libre sin backend" in prompt
    assert "No inventes UUIDs" in prompt
    assert "unknown" in prompt  # fail closed


def test_system_prompt_handles_multi_intent_limitation():
    prompt = build_system_prompt([], [], [], [], [])
    assert "múltiples intenciones complejas" in prompt
    assert "unknown" in prompt


def test_unsupported_advanced_advice_is_safely_bounded():
    assert is_read_intent("ask_advice")
