from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.categories.enums import CategoryType, ConfigurableCategoryType


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    type: ConfigurableCategoryType
    icon_key: str | None = Field(None, pattern=r"^[a-z0-9_]{1,50}$")

    @field_validator("name")
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("El nombre no puede estar vacío.")
        return v


class CategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1)
    icon_key: str | None = Field(None, pattern=r"^[a-z0-9_]{1,50}$")
    is_active: bool | None = Field(
        None, description="Legacy compatibility. Prefer /archive or /restore."
    )

    @field_validator("name")
    def name_not_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("El nombre no puede estar vacío.")
        return v


class CategoryRead(BaseModel):
    id: UUID
    is_global: bool = False
    user_id: UUID | None
    name: str
    type: CategoryType | None
    is_active: bool
    icon_key: str | None = None
    stable_key: str | None = None

    model_config = ConfigDict(from_attributes=True)
