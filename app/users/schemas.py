from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserRead(BaseModel):
    id: UUID
    name: str | None
    email: str | None
    timezone: str
    currency: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class UserOnboardingRequest(BaseModel):
    name: str | None = None
    timezone: str = "America/Bogota"
    currency: str = Field(min_length=3, max_length=3)


class UserOnboardingResponse(BaseModel):
    created: bool
    onboarding_completed: bool
    profile: UserRead
    next_step: str


class UserMeResponse(BaseModel):
    profile: UserRead
    onboarding_completed: bool
    has_accounts: bool
    next_step: str
