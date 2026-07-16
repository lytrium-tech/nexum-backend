import uuid
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    type: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default="true")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    stable_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "type = ANY (ARRAY['income', 'expense', 'credit_card', "
            "'obligation', 'goal', 'transfer', 'system'])",
            name="categories_type_check",
        ),
        UniqueConstraint(
            "user_id", "type", "normalized_name", name="uq_category_user_type_normalized_name"
        ),
        Index(
            "idx_categories_stable_key_unique",
            "stable_key",
            unique=True,
            postgresql_where="stable_key IS NOT NULL",
        ),
        Index(
            "idx_categories_global_type_norm_name_unique",
            "type",
            "normalized_name",
            unique=True,
            postgresql_where="user_id IS NULL",
        ),
        CheckConstraint(
            "((user_id IS NULL AND stable_key IS NOT NULL) OR (user_id IS NOT NULL AND stable_key IS NULL))",
            name="chk_categories_global_or_private",
        ),
    )

    @property
    def is_global(self) -> bool:
        return self.user_id is None
