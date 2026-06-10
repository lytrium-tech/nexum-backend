import uuid
from unittest.mock import AsyncMock

import pytest

from app.categories.enums import CategoryType
from app.categories.exceptions import CategoryForbiddenError
from app.categories.models import Category
from app.categories.repository import CategoryRepository
from app.categories.schemas import CategoryCreate, CategoryUpdate
from app.categories.service import CategoryService
from app.users.service import UserService


@pytest.fixture
def mock_repo():
    repo = AsyncMock(spec=CategoryRepository)
    repo.session = AsyncMock()
    return repo


@pytest.fixture
def mock_user_service():
    return AsyncMock(spec=UserService)


@pytest.fixture
def service(mock_repo, mock_user_service):
    return CategoryService(mock_repo, mock_user_service)


@pytest.mark.asyncio
async def test_create_category_success(service, mock_repo):
    user_id = uuid.uuid4()
    payload = CategoryCreate(name="  Árbol  ", type=CategoryType.INCOME)
    mock_repo.check_name_exists.return_value = False

    async def fake_create(cat):
        cat.id = uuid.uuid4()
        return cat

    mock_repo.create.side_effect = fake_create

    result = await service.create_category(user_id, payload)

    assert result.name == "Árbol"
    assert result.user_id == user_id
    mock_repo.check_name_exists.assert_called_once_with(user_id, "arbol")


@pytest.mark.asyncio
async def test_update_global_category_forbidden(service, mock_repo):
    user_id = uuid.uuid4()
    cat_id = uuid.uuid4()
    mock_repo.get_by_id.return_value = Category(id=cat_id, user_id=None, name="System")

    with pytest.raises(CategoryForbiddenError):
        await service.update_category(user_id, cat_id, CategoryUpdate(name="New Name"))


@pytest.mark.asyncio
async def test_delete_global_category_forbidden(service, mock_repo):
    user_id = uuid.uuid4()
    cat_id = uuid.uuid4()
    mock_repo.get_by_id.return_value = Category(id=cat_id, user_id=None, name="System")

    with pytest.raises(CategoryForbiddenError):
        await service.delete_category(user_id, cat_id)


@pytest.mark.asyncio
async def test_delete_private_category_success(service, mock_repo):
    user_id = uuid.uuid4()
    cat_id = uuid.uuid4()
    cat = Category(id=cat_id, user_id=user_id, is_active=True)
    mock_repo.get_by_id.return_value = cat

    await service.delete_category(user_id, cat_id)
    assert cat.is_active is False
    mock_repo.session.flush.assert_called_once()
