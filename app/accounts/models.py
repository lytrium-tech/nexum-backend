import uuid
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str | None] = mapped_column(Text, nullable=True)
    balance: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True, server_default="0")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="COP")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("balance >= 0", name="accounts_balance_check"),
        CheckConstraint(
            "type = ANY (ARRAY['bank', 'wallet', 'cash', 'savings'])", name="accounts_type_check"
        ),
    )

    @property
    def available_balance(self) -> Decimal:
        if hasattr(self, "_available_balance"):
            return self._available_balance
        return self.balance or Decimal("0.00")

    @available_balance.setter
    def available_balance(self, value: Decimal) -> None:
        self._available_balance = value
