from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UserRead(BaseModel):
    id: UUID
    name: str | None
    email: str | None
    timezone: str
    currency: str
    status: str

    model_config = ConfigDict(from_attributes=True)
