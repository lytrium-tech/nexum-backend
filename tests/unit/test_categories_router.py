import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.categories.models import Category
from app.core.database import get_db_session
from app.core.security import AuthenticatedIdentity, get_current_user
from app.main import app
from app.users.dependencies import get_current_user_profile_dep
from app.users.schemas import UserRead

fake_user_id = str(uuid.uuid4())


def override_get_current_user():
    return AuthenticatedIdentity(user_id=fake_user_id, is_dev=True)


def override_get_current_user_profile():
    return UserRead(
        id=uuid.UUID(fake_user_id),
        email="test@test.com",
        is_active=True,
        created_at=None,
        updated_at=None,
        name="Test User",
        timezone="UTC",
        currency="USD",
        status="active",
    )


app.dependency_overrides[get_current_user] = override_get_current_user
app.dependency_overrides[get_current_user_profile_dep] = override_get_current_user_profile


@pytest.fixture
def mock_db_session():
    mock_session = AsyncMock(spec=AsyncSession)
    return mock_session


@pytest.fixture
def override_db(mock_db_session):
    app.dependency_overrides[get_db_session] = lambda: mock_db_session
    yield mock_db_session
    app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture
async def client(override_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def create_mock_execute_result(scalar_value=None, first_value=None):
    mock_result = MagicMock()
    # It must return a MagicMock, not a coroutine!
    # By default, mock_result is a MagicMock, so scalar_one_or_none returns a MagicMock... wait
    mock_result.scalar_one_or_none.return_value = scalar_value
    mock_result.scalars.return_value.all.return_value = [scalar_value] if scalar_value else []
    mock_result.first.return_value = first_value
    return mock_result


@pytest.mark.asyncio
async def test_api_create_category_success(client, mock_db_session):
    # For check_name_exists
    mock_db_session.execute.return_value = create_mock_execute_result(first_value=None)

    def mock_add(obj):
        if hasattr(obj, "id") and getattr(obj, "id") is None:
            obj.id = uuid.uuid4()

    mock_db_session.add.side_effect = mock_add

    response = await client.post(
        "/api/v1/categories", json={"name": "Test", "type": "income", "icon_key": "test_icon"}
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Test"


@pytest.mark.asyncio
async def test_api_create_category_whitespace(client):
    response = await client.post("/api/v1/categories", json={"name": "   ", "type": "income"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_api_create_category_internal_type(client):
    response = await client.post("/api/v1/categories", json={"name": "Test", "type": "system"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_api_create_category_extra_field(client):
    response = await client.post(
        "/api/v1/categories", json={"name": "Test", "type": "income", "extra": "forbidden"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_api_create_category_duplicate(client, mock_db_session):
    mock_db_session.execute.return_value = create_mock_execute_result(first_value=("exists",))
    response = await client.post("/api/v1/categories", json={"name": "Test", "type": "income"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_api_update_category_success(client, mock_db_session):
    cat_id = uuid.uuid4()
    mock_cat = Category(
        id=cat_id, user_id=uuid.UUID(fake_user_id), name="Old", type="income", is_active=True
    )

    async def execute_side_effect(stmt, *args, **kwargs):
        stmt_str = str(stmt).lower()
        if "from categories" in stmt_str and "where categories.id =" in stmt_str:
            return create_mock_execute_result(scalar_value=mock_cat)
        if "count" not in stmt_str and "categories.normalized_name" in stmt_str:
            return create_mock_execute_result(first_value=None)
        return create_mock_execute_result(scalar_value=mock_cat)

    mock_db_session.execute.side_effect = execute_side_effect

    response = await client.patch(
        f"/api/v1/categories/{cat_id}", json={"name": "Renamed", "icon_key": "new_icon"}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


@pytest.mark.asyncio
async def test_api_update_category_is_active_legacy(client, mock_db_session):
    cat_id = uuid.uuid4()
    mock_cat = Category(
        id=cat_id, user_id=uuid.UUID(fake_user_id), name="Test", type="income", is_active=True
    )

    async def execute_side_effect(stmt, *args, **kwargs):
        return create_mock_execute_result(scalar_value=mock_cat)

    mock_db_session.execute.side_effect = execute_side_effect

    response = await client.patch(f"/api/v1/categories/{cat_id}", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    response = await client.patch(f"/api/v1/categories/{cat_id}", json={"is_active": True})
    assert response.status_code == 200
    assert response.json()["is_active"] is True


@pytest.mark.asyncio
async def test_api_update_category_forbidden_global(client, mock_db_session):
    cat_id = uuid.uuid4()
    mock_cat = Category(id=cat_id, user_id=None, name="Global", type="income", is_active=True)

    async def execute_side_effect(stmt, *args, **kwargs):
        return create_mock_execute_result(scalar_value=mock_cat)

    mock_db_session.execute.side_effect = execute_side_effect

    response = await client.patch(f"/api/v1/categories/{cat_id}", json={"name": "Hacked"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_api_update_category_not_found(client, mock_db_session):
    async def execute_side_effect(stmt, *args, **kwargs):
        return create_mock_execute_result(scalar_value=None)

    mock_db_session.execute.side_effect = execute_side_effect

    response = await client.patch(f"/api/v1/categories/{uuid.uuid4()}", json={"name": "Hacked"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_update_category_extra_field(client):
    response = await client.patch(
        f"/api/v1/categories/{uuid.uuid4()}", json={"stable_key": "hacked"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_api_archive_restore_category(client, mock_db_session):
    cat_id = uuid.uuid4()
    mock_cat = Category(
        id=cat_id, user_id=uuid.UUID(fake_user_id), name="Old", type="income", is_active=True
    )

    async def execute_side_effect(stmt, *args, **kwargs):
        return create_mock_execute_result(scalar_value=mock_cat)

    mock_db_session.execute.side_effect = execute_side_effect

    res_archive = await client.post(f"/api/v1/categories/{cat_id}/archive")
    assert res_archive.status_code == 200

    res_archive_repeat = await client.post(f"/api/v1/categories/{cat_id}/archive")
    assert res_archive_repeat.status_code == 200

    res_restore = await client.post(f"/api/v1/categories/{cat_id}/restore")
    assert res_restore.status_code == 200


@pytest.mark.asyncio
async def test_api_delete_category_deprecated(client, mock_db_session):
    cat_id = uuid.uuid4()
    mock_cat = Category(
        id=cat_id, user_id=uuid.UUID(fake_user_id), name="Old", type="income", is_active=True
    )

    async def execute_side_effect(stmt, *args, **kwargs):
        return create_mock_execute_result(scalar_value=mock_cat)

    mock_db_session.execute.side_effect = execute_side_effect

    res = await client.delete(f"/api/v1/categories/{cat_id}")
    assert res.status_code == 204
