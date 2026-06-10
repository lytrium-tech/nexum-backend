from enum import StrEnum


class CategoryType(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"
    CREDIT_CARD = "credit_card"
    OBLIGATION = "obligation"
    GOAL = "goal"
    TRANSFER = "transfer"
    SYSTEM = "system"
