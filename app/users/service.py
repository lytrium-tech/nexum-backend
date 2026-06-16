from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.errors import NotFoundError
from app.core.security import AuthenticatedIdentity
from app.core.utils import clean_presentation_name
from app.users.models import User
from app.users.repository import UserRepository
from app.users.schemas import UserOnboardingRequest, UserOnboardingResponse, UserRead


class UserService:
    def __init__(self, repository: UserRepository):
        self.repository = repository

    async def get_current_user_profile(self, auth_user: AuthenticatedIdentity) -> UserRead:
        if auth_user.is_dev:
            # Bypass development
            user = await self.repository.get_by_id(UUID(auth_user.user_id))
        else:
            user = await self.repository.get_by_auth_id(auth_user.user_id)

        if not user:
            raise NotFoundError(
                message="Perfil de usuario no encontrado en base de datos transaccional."
            )

        return UserRead.model_validate(user)

    async def onboard_user(
        self, identity: AuthenticatedIdentity, payload: UserOnboardingRequest
    ) -> UserOnboardingResponse:
        if identity.is_dev:
            user = await self.repository.get_by_id(UUID(identity.user_id))
        else:
            user = await self.repository.get_by_auth_id(identity.user_id)

        if user:
            return UserOnboardingResponse(
                created=False,
                onboarding_completed=True,
                profile=UserRead.model_validate(user),
                next_step="create_account",
            )

        try:
            db_user = User(
                auth_user_id=UUID(identity.user_id) if not identity.is_dev else None,
                email=identity.email,
                name=clean_presentation_name(payload.name),
                timezone=payload.timezone,
                currency=payload.currency,
                status="active",
            )
            created = await self.repository.create_user(db_user)
            await self.repository.session.flush()
            return UserOnboardingResponse(
                created=True,
                onboarding_completed=True,
                profile=UserRead.model_validate(created),
                next_step="create_account",
            )
        except IntegrityError:
            if identity.is_dev:
                user = await self.repository.get_by_id(UUID(identity.user_id))
            else:
                user = await self.repository.get_by_auth_id(identity.user_id)
            return UserOnboardingResponse(
                created=False,
                onboarding_completed=True,
                profile=UserRead.model_validate(user),
                next_step="create_account",
            )
