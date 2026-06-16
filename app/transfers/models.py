"""
app/transfers/models.py
=======================
Modelo SQLAlchemy para la tabla public.transfers
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.accounts.models import Account
from app.core.database import Base


class Transfer(Base):
    """
    Representa un movimiento de dinero entre dos cuentas del mismo usuario.
    """

    __tablename__ = "transfers"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    destination_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="COP")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    command_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), unique=True, nullable=True
    )
    source_message_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    raw_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="completed")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    source_account: Mapped["Account"] = relationship(
        "Account", foreign_keys=[source_account_id], lazy="joined"
    )
    destination_account: Mapped["Account"] = relationship(
        "Account", foreign_keys=[destination_account_id], lazy="joined"
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="transfers_amount_check"),
        CheckConstraint(
            "source_account_id != destination_account_id",
            name="check_different_accounts",
        ),
    )
