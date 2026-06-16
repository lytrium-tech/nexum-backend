import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversationalRequest(BaseModel):
    """Payload de entrada del endpoint POST /api/v1/conversations/message"""

    message: str = Field(..., min_length=1)
    channel: Literal["api", "pwa", "whatsapp_future"] = "api"
    external_message_id: str | None = None
    pending_action_id: uuid.UUID | None = None


class ConversationalResponse(BaseModel):
    """Payload de salida del endpoint POST /api/v1/conversations/message"""

    response_text: str
    intent: str
    status: Literal[
        "completed", "awaiting_clarification", "awaiting_confirmation", "cancelled", "error"
    ]
    trace_id: uuid.UUID
    pending_action_id: uuid.UUID | None = None
    structured_data: dict[str, Any] | None = None


# ────────────────────────────────────────────────────────────────────────────
# Esquemas NLU para Structured Outputs (Gemini)
# Gemini retornará estrictamente este formato JSON.
# ────────────────────────────────────────────────────────────────────────────


class ExtractedEntities(BaseModel):
    """Entidades extraídas por Gemini desde el mensaje del usuario."""

    amount: Decimal | None = Field(
        None, description="Monto numérico extraído de la solicitud. Ej. 50000, 100.50"
    )
    account: str | None = Field(
        None, description="Nombre de la cuenta mencionada. Ej. Nequi, Bancolombia"
    )
    source_account: str | None = Field(
        None, description="Nombre de la cuenta de origen para transferencias. Ej. Nequi"
    )
    destination_account: str | None = Field(
        None, description="Nombre de la cuenta de destino para transferencias. Ej. Bancolombia"
    )
    category: str | None = Field(
        None, description="Nombre de la categoría mencionada. Ej. Comida, Transporte"
    )
    credit_card: str | None = Field(
        None, description="Nombre de la tarjeta de crédito. Ej. RappiCard, Nu"
    )
    goal: str | None = Field(None, description="Nombre de la meta mencionada. Ej. Viaje, iPhone")
    obligation: str | None = Field(
        None, description="Nombre de la obligación mencionada. Ej. Arriendo"
    )
    installments_total: int | None = Field(
        None, description="Número de cuotas de una compra con TC. Ej. 1, 12, 36"
    )
    description: str | None = Field(None, description="Descripción adicional del gasto o ingreso.")
    target_date: str | None = Field(
        None, description="Fecha objetivo en formato YYYY-MM-DD para metas."
    )


class GeminiNLUOutput(BaseModel):
    """Salida estructurada requerida desde Gemini."""

    intent: Literal[
        "ask_balance",
        "ask_free_money",
        "ask_debt",
        "ask_cashflow",
        "ask_goals",
        "ask_obligations",
        "ask_financial_snapshot",
        "create_income",
        "create_expense",
        "create_goal",
        "create_goal_contribution",
        "create_obligation",
        "create_obligation_payment",
        "create_credit_card_purchase",
        "create_credit_card_payment",
        "create_transfer",
        "confirm_action",
        "cancel_action",
        "clarify_action",
        "unknown",
    ] = Field(..., description="La intención detectada en el mensaje del usuario.")
    entities: ExtractedEntities = Field(
        ..., description="Entidades extraídas para cumplir la intención."
    )
    confidence: float = Field(..., description="Confianza de la predicción, entre 0 y 1.")


# ────────────────────────────────────────────────────────────────────────────
# Esquemas Internos / BD
# ────────────────────────────────────────────────────────────────────────────


class PendingActionBase(BaseModel):
    user_id: uuid.UUID
    intent: str
    data: dict[str, Any]
    missing_fields: dict[str, Any] | None = None
    status: str
    command_id: uuid.UUID | None = None
    expires_at: datetime | None = None
    source_message_id: uuid.UUID | None = None
    confirmation_message_id: uuid.UUID | None = None


class PendingActionRead(PendingActionBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
