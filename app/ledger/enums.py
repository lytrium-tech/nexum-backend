"""
app/ledger/enums.py
===================
Enumeraciones tipadas del dominio Ledger.
Reflejan exactamente los CHECK constraints de PostgreSQL en public.financial_events.
"""

from enum import StrEnum


class EventType(StrEnum):
    """
    Catálogo central de eventos financieros de Nexum.
    No define impactos financieros directamente, solo categoriza el evento.
    """

    INCOME = "income"
    EXPENSE = "expense"
    GOAL_CONTRIBUTION = "goal_contribution"
    OBLIGATION_PAYMENT = "obligation_payment"
    CREDIT_CARD_PURCHASE = "credit_card_purchase"
    CREDIT_CARD_PAYMENT = "credit_card_payment"
    MANUAL_ADJUSTMENT = "manual_adjustment"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"


class Direction(StrEnum):
    """
    Dirección del flujo de caja o impacto primario.
    """

    INFLOW = "inflow"
    OUTFLOW = "outflow"
    NEUTRAL = "neutral"
