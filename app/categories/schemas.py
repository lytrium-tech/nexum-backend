from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.categories.enums import CategoryType


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1)
    type: CategoryType


class CategoryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)


class CategoryRead(BaseModel):
    id: UUID
    user_id: UUID | None
    name: str
    type: CategoryType | None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)
