from uuid import UUID

from app.core.errors import NotFoundError
from app.core.security import AuthenticatedUser
from app.users.repository import UserRepository
from app.users.schemas import UserRead


class UserService:
    def __init__(self, repository: UserRepository):
        self.repository = repository

    async def get_current_user_profile(self, auth_user: AuthenticatedUser) -> UserRead:
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
