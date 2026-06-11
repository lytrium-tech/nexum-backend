from datetime import datetime
import uuid
from uuid import UUID

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )
    auth_user_id: Mapped[UUID | None] = mapped_column(nullable=True, unique=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="America/Bogota")
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="COP")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
