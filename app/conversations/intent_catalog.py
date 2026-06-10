from typing import Literal

# Catálogo estático de tipos
IntentType = Literal[
    "ask_balance", "ask_free_money", "ask_debt", "ask_cashflow",
    "ask_goals", "ask_obligations", "ask_financial_snapshot",
    "create_income", "create_expense", "create_goal", "create_goal_contribution",
    "create_obligation", "create_obligation_payment",
    "create_credit_card_purchase", "create_credit_card_payment",
    "confirm_action", "cancel_action", "clarify_action", "unknown"
]

def is_read_intent(intent: str) -> bool:
    return intent in {
        "ask_balance", "ask_free_money", "ask_debt", "ask_cashflow",
        "ask_goals", "ask_obligations", "ask_financial_snapshot"
    }

def is_write_intent(intent: str) -> bool:
    return intent in {
        "create_income", "create_expense", "create_goal", "create_goal_contribution",
        "create_obligation", "create_obligation_payment",
        "create_credit_card_purchase", "create_credit_card_payment"
    }

def is_conversational_control_intent(intent: str) -> bool:
    return intent in {
        "confirm_action", "cancel_action", "clarify_action", "unknown"
    }
