from uuid import UUID

from app.categories.exceptions import CategoryDuplicateError, CategoryForbiddenError
from app.categories.models import Category
from app.categories.repository import CategoryRepository
from app.categories.schemas import CategoryCreate, CategoryRead, CategoryUpdate
from app.core.errors import NotFoundError
from app.core.utils import clean_presentation_name, normalize_name


class CategoryService:
    def __init__(self, repository: CategoryRepository):
        self.repository = repository
    async def list_categories(self, auth_user_id: UUID) -> list[CategoryRead]:
        categories = await self.repository.list_available(auth_user_id)
        return [CategoryRead.model_validate(c) for c in categories]

    async def create_category(self, auth_user_id: UUID, payload: CategoryCreate) -> CategoryRead:
        norm_name = normalize_name(payload.name)
        if not norm_name:
            raise ValueError("El nombre no puede estar vacío.")

        exists = await self.repository.check_name_exists(auth_user_id, norm_name)
        if exists:
            raise CategoryDuplicateError()

        db_category = Category(
            user_id=auth_user_id,
            name=clean_presentation_name(payload.name),
            type=payload.type.value,
            is_active=True,
        )
        created = await self.repository.create(db_category)
        return CategoryRead.model_validate(created)

    async def update_category(
        self, auth_user_id: UUID, category_id: UUID, payload: CategoryUpdate
    ) -> CategoryRead:
        category = await self._get_category_or_404(category_id)

        # Proteger globales (user_id is None) y ajenas
        if category.user_id is None or category.user_id != auth_user_id:
            raise CategoryForbiddenError()

        if payload.name is not None:
            norm_name = normalize_name(payload.name)
            if not norm_name:
                raise ValueError("El nombre no puede estar vacío.")
            if normalize_name(category.name) != norm_name:
                exists = await self.repository.check_name_exists(auth_user_id, norm_name)
                if exists:
                    raise CategoryDuplicateError()
            category.name = clean_presentation_name(payload.name)

        if payload.is_active is not None:
            category.is_active = payload.is_active

        await self.repository.session.flush()
        return CategoryRead.model_validate(category)

    async def delete_category(self, auth_user_id: UUID, category_id: UUID) -> None:
        category = await self._get_category_or_404(category_id)

        if category.user_id is None or category.user_id != auth_user_id:
            raise CategoryForbiddenError()

        category.is_active = False
        await self.repository.session.flush()

    async def _get_category_or_404(self, category_id: UUID) -> Category:
        category = await self.repository.get_by_id(category_id)
        if not category:
            raise NotFoundError(message="Categoría no encontrada.")
        return category
