from uuid import UUID

from app.categories.enums import ConfigurableCategoryType
from app.categories.exceptions import CategoryDuplicateError, CategoryForbiddenError
from app.categories.models import Category
from app.categories.repository import CategoryRepository
from app.categories.schemas import CategoryCreate, CategoryRead, CategoryUpdate
from app.core.errors import NotFoundError, ValidationError
from app.core.utils import clean_presentation_name, normalize_name


class CategoryService:
    def __init__(self, repository: CategoryRepository):
        self.repository = repository

    async def list_categories(
        self, auth_user_id: UUID, include_inactive: bool = False, type_: str | None = None
    ) -> list[CategoryRead]:
        categories = await self.repository.list_available(auth_user_id, include_inactive, type_)
        return [CategoryRead.model_validate(c) for c in categories]

    async def create_category(self, auth_user_id: UUID, payload: CategoryCreate) -> CategoryRead:
        norm_name = normalize_name(payload.name)
        if not norm_name:
            raise ValidationError(message="El nombre no puede estar vacío.")

        if payload.type.value not in (
            ConfigurableCategoryType.INCOME.value,
            ConfigurableCategoryType.EXPENSE.value,
        ):
            raise ValidationError(message="Tipo de categoría no permitido para creación.")

        exists = await self.repository.check_name_exists(
            auth_user_id, norm_name, payload.type.value
        )
        if exists:
            raise CategoryDuplicateError()

        db_category = Category(
            user_id=auth_user_id,
            name=clean_presentation_name(payload.name),
            normalized_name=norm_name,
            type=payload.type.value,
            icon_key=payload.icon_key,
            is_active=True,
        )
        created = await self.repository.create(db_category)
        return CategoryRead.model_validate(created)

    async def update_category(
        self, auth_user_id: UUID, category_id: UUID, payload: CategoryUpdate
    ) -> CategoryRead:
        category = await self._get_category_or_404(category_id)

        if category.user_id is None or category.user_id != auth_user_id:
            raise CategoryForbiddenError()

        if category.type not in (
            ConfigurableCategoryType.INCOME.value,
            ConfigurableCategoryType.EXPENSE.value,
        ):
            raise CategoryForbiddenError(
                message="No se pueden modificar categorías de sistema o internas legacy."
            )

        if payload.name is not None:
            norm_name = normalize_name(payload.name)
            if not norm_name:
                raise ValidationError(message="El nombre no puede estar vacío.")
            if category.normalized_name != norm_name:
                exists = await self.repository.check_name_exists(
                    auth_user_id, norm_name, category.type
                )
                if exists:
                    raise CategoryDuplicateError()
            category.name = clean_presentation_name(payload.name)
            category.normalized_name = norm_name

        if payload.icon_key is not None:
            category.icon_key = payload.icon_key

        if payload.is_active is not None:
            if payload.is_active:
                await self.restore_category(auth_user_id, category_id)
            else:
                await self.archive_category(auth_user_id, category_id)

        await self.repository.session.flush()
        return CategoryRead.model_validate(category)

    async def archive_category(self, auth_user_id: UUID, category_id: UUID) -> CategoryRead:
        category = await self._get_category_or_404(category_id)
        if category.user_id is None or category.user_id != auth_user_id:
            raise CategoryForbiddenError()

        if category.type not in (
            ConfigurableCategoryType.INCOME.value,
            ConfigurableCategoryType.EXPENSE.value,
        ):
            raise CategoryForbiddenError(
                message="No se pueden archivar categorías de sistema o internas legacy."
            )

        category.is_active = False
        await self.repository.session.flush()
        return CategoryRead.model_validate(category)

    async def restore_category(self, auth_user_id: UUID, category_id: UUID) -> CategoryRead:
        category = await self._get_category_or_404(category_id)
        if category.user_id is None or category.user_id != auth_user_id:
            raise CategoryForbiddenError()

        if category.type not in (
            ConfigurableCategoryType.INCOME.value,
            ConfigurableCategoryType.EXPENSE.value,
        ):
            raise CategoryForbiddenError(
                message="No se pueden restaurar categorías de sistema o internas legacy."
            )

        category.is_active = True
        await self.repository.session.flush()
        return CategoryRead.model_validate(category)

    async def delete_category(self, auth_user_id: UUID, category_id: UUID) -> None:
        await self.archive_category(auth_user_id, category_id)

    async def _get_category_or_404(self, category_id: UUID) -> Category:
        category = await self.repository.get_by_id(category_id)
        if not category:
            raise NotFoundError(message="Categoría no encontrada.")
        return category
