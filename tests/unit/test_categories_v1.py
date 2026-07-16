import importlib.util
import os
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.categories.enums import ConfigurableCategoryType
from app.categories.exceptions import CategoryForbiddenError
from app.categories.models import Category
from app.categories.repository import CategoryRepository
from app.categories.schemas import CategoryCreate, CategoryUpdate
from app.categories.service import CategoryService
from app.core.errors import ValidationError


@pytest.fixture
def mock_repo():
    repo = AsyncMock(spec=CategoryRepository)
    repo.session = AsyncMock()
    return repo


@pytest.fixture
def service(mock_repo):
    return CategoryService(mock_repo)


@pytest.mark.asyncio
async def test_create_income_expense_success(service, mock_repo):
    user_id = uuid.uuid4()
    payload = CategoryCreate(name="Salary", type=ConfigurableCategoryType.INCOME, icon_key="salary")
    mock_repo.check_name_exists.return_value = False

    async def fake_create(cat):
        cat.id = uuid.uuid4()
        return cat

    mock_repo.create.side_effect = fake_create

    result = await service.create_category(user_id, payload)
    assert result.name == "Salary"
    assert result.type == "income"
    assert result.icon_key == "salary"


@pytest.mark.asyncio
async def test_create_internal_types_rejected(service, mock_repo):
    user_id = uuid.uuid4()

    class FakePayload:
        name = "Test"
        type = type("obj", (object,), {"value": "system"})
        icon_key = None

    with pytest.raises(ValidationError):
        await service.create_category(user_id, FakePayload())


@pytest.mark.asyncio
async def test_archive_restore_category(service, mock_repo):
    user_id = uuid.uuid4()
    cat_id = uuid.uuid4()
    cat = Category(id=cat_id, user_id=user_id, is_active=True, name="dummy", type="income")
    mock_repo.get_by_id.return_value = cat

    await service.archive_category(user_id, cat_id)
    assert cat.is_active is False

    await service.restore_category(user_id, cat_id)
    assert cat.is_active is True


@pytest.mark.asyncio
async def test_delete_delegates_to_archive(service, mock_repo):
    user_id = uuid.uuid4()
    cat_id = uuid.uuid4()
    cat = Category(id=cat_id, user_id=user_id, is_active=True, name="dummy", type="income")
    mock_repo.get_by_id.return_value = cat

    await service.delete_category(user_id, cat_id)
    assert cat.is_active is False


@pytest.mark.asyncio
async def test_protect_legacy_internal_types(service, mock_repo):
    user_id = uuid.uuid4()
    cat_id = uuid.uuid4()
    cat = Category(id=cat_id, user_id=user_id, is_active=True, name="legacy_system", type="system")
    mock_repo.get_by_id.return_value = cat

    with pytest.raises(CategoryForbiddenError):
        await service.update_category(user_id, cat_id, CategoryUpdate(name="Renamed"))

    with pytest.raises(CategoryForbiddenError):
        await service.archive_category(user_id, cat_id)

    with pytest.raises(CategoryForbiddenError):
        await service.restore_category(user_id, cat_id)

    with pytest.raises(CategoryForbiddenError):
        await service.delete_category(user_id, cat_id)


@pytest.mark.asyncio
async def test_update_category_isolated_fields(service, mock_repo):
    user_id = uuid.uuid4()
    cat_id = uuid.uuid4()
    cat = Category(
        id=cat_id, user_id=user_id, is_active=True, name="dummy", type="income", icon_key="old"
    )
    mock_repo.get_by_id.return_value = cat
    mock_repo.check_name_exists.return_value = False

    # Name isolated
    res1 = await service.update_category(user_id, cat_id, CategoryUpdate(name="Renamed"))
    assert res1.name == "Renamed"
    assert res1.icon_key == "old"

    # Icon isolated
    res2 = await service.update_category(user_id, cat_id, CategoryUpdate(icon_key="new_icon"))
    assert res2.name == "Renamed"
    assert res2.icon_key == "new_icon"

    # Combination
    res3 = await service.update_category(
        user_id, cat_id, CategoryUpdate(name="Combined", icon_key="combined_icon", is_active=False)
    )
    assert res3.name == "Combined"
    assert res3.icon_key == "combined_icon"
    assert res3.is_active is False


def _load_migration():
    file_path = os.path.abspath("alembic/versions/categories_v1_phase1.py")
    spec = importlib.util.spec_from_file_location("categories_v1_phase1", file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["categories_v1_phase1"] = module
    spec.loader.exec_module(module)
    return module


def _category(
    *,
    id=None,
    user_id=None,
    type="expense",
    name="Other",
    normalized_name="other",
    stable_key=None,
):
    return SimpleNamespace(
        id=id or uuid.uuid4(),
        user_id=user_id,
        type=type,
        name=name,
        normalized_name=normalized_name,
        stable_key=stable_key,
    )


def _index(name, *, unique=True, columns=None, predicate="stable_key IS NOT NULL"):
    return {
        "name": name,
        "unique": unique,
        "column_names": columns or ["stable_key"],
        "dialect_options": {"postgresql_where": predicate},
    }


def test_index_definition_accepts_canonical_and_rejects_invalid_variants():
    mod = _load_migration()
    validate = mod._validate_index_definition
    kwargs = {
        "expected_name": "idx_categories_stable_key_unique",
        "expected_columns": ["stable_key"],
        "expected_predicate": "stable_key IS NOT NULL",
    }
    validate(_index("idx_categories_stable_key_unique"), **kwargs)
    validate(
        _index("idx_categories_stable_key_unique", predicate='(("stable_key" IS NOT NULL))'),
        **kwargs,
    )
    invalid = [
        _index("idx_categories_stable_key_unique", unique=False),
        _index("idx_categories_stable_key_unique", columns=["icon_key"]),
        _index("idx_categories_stable_key_unique", columns=["normalized_name", "stable_key"]),
        _index("idx_categories_stable_key_unique", predicate="stable_key IS NULL"),
        _index(
            "idx_categories_stable_key_unique",
            predicate="stable_key IS NOT NULL OR user_id IS NULL",
        ),
    ]
    for index in invalid:
        with pytest.raises(Exception):
            validate(index, **kwargs)


def test_global_index_rejects_wrong_order():
    mod = _load_migration()
    with pytest.raises(Exception):
        mod._validate_index_definition(
            _index(
                "idx_categories_global_type_norm_name_unique",
                columns=["normalized_name", "type"],
                predicate="user_id IS NULL",
            ),
            expected_name="idx_categories_global_type_norm_name_unique",
            expected_columns=["type", "normalized_name"],
            expected_predicate="user_id IS NULL",
        )


def test_constraint_definition_accepts_equivalent_syntax():
    mod = _load_migration()
    canonical = {
        "name": "chk_categories_global_or_private",
        "sqltext": mod.GLOBAL_PRIVATE_CONSTRAINT,
    }
    quoted = {
        "name": "chk_categories_global_or_private",
        "sqltext": '(((("user_id" IS NULL AND "stable_key" IS NOT NULL) '
        'OR ("user_id" IS NOT NULL AND "stable_key" IS NULL))))',
    }
    reversed_branches = {
        "name": "chk_categories_global_or_private",
        "sqltext": "((user_id IS NOT NULL AND stable_key IS NULL) OR "
        "(user_id IS NULL AND stable_key IS NOT NULL))",
    }
    mod._validate_constraint_definition(canonical)
    mod._validate_constraint_definition(quoted)
    mod._validate_constraint_definition(reversed_branches)


@pytest.mark.parametrize(
    "sqltext",
    [
        "user_id IS NULL AND stable_key IS NOT NULL",
        "user_id IS NOT NULL AND stable_key IS NULL",
        "((user_id IS NULL OR stable_key IS NOT NULL) OR "
        "(user_id IS NOT NULL AND stable_key IS NULL))",
        "((user_id IS NULL AND stable_key IS NOT NULL) AND "
        "(user_id IS NOT NULL AND stable_key IS NULL))",
        "((user_id IS NULL AND stable_key IS NULL) OR "
        "(user_id IS NOT NULL AND stable_key IS NOT NULL))",
        "user_id IS NULL AND (stable_key IS NOT NULL OR user_id IS NOT NULL) "
        "AND stable_key IS NULL",
        "(user_id IS NULL AND stable_key IS NOT NULL OR user_id IS NOT NULL) "
        "AND stable_key IS NULL",
        "((user_id IS NULL AND stable_key IS NOT NULL AND type IS NOT NULL) OR "
        "(user_id IS NOT NULL AND stable_key IS NULL))",
        "((user_id IS NULL OR stable_key IS NOT NULL) OR "
        "(user_id IS NOT NULL AND stable_key IS NULL))",
    ],
)
def test_constraint_definition_rejects_incomplete_or_inverted_logic(sqltext):
    mod = _load_migration()
    with pytest.raises(Exception):
        mod._validate_constraint_definition(
            {"name": "chk_categories_global_or_private", "sqltext": sqltext}
        )


def test_downgrade_is_a_non_mutating_no_op(monkeypatch):
    mod = _load_migration()
    mocked_op = MagicMock()
    monkeypatch.setattr(mod, "op", mocked_op)

    assert mod.downgrade() is None
    assert mocked_op.method_calls == []
    mocked_op.drop_index.assert_not_called()
    mocked_op.drop_constraint.assert_not_called()
    mocked_op.drop_column.assert_not_called()
    mocked_op.execute.assert_not_called()


def test_legacy_matches_name_or_normalized_name_and_preserves_id():
    mod = _load_migration()
    name_id = uuid.uuid4()
    normalized_id = uuid.uuid4()
    by_name = _category(id=name_id, name="sin_clasificar", normalized_name="legacy")
    by_normalized = _category(
        id=normalized_id, name="Legacy expense", normalized_name="sin_clasificar"
    )
    assert mod._resolve_catalog_matches([by_name])["expense_uncategorized"] == name_id
    assert mod._resolve_catalog_matches([by_normalized])["expense_uncategorized"] == normalized_id


def test_legacy_reconciliation_does_not_affect_income_uncategorized():
    mod = _load_migration()
    expense_id = uuid.uuid4()
    income_id = uuid.uuid4()
    rows = [
        _category(id=expense_id, name="sin_clasificar", normalized_name="legacy"),
        _category(
            id=income_id,
            type="income",
            name="Sin clasificar",
            normalized_name="sin clasificar",
            stable_key="income_uncategorized",
        ),
    ]
    matches = mod._resolve_catalog_matches(rows)
    assert matches["expense_uncategorized"] == expense_id
    assert matches["income_uncategorized"] == income_id


def test_legacy_conflicts_with_existing_stable_key_before_reconciliation():
    mod = _load_migration()
    official = _category(
        name="Official expense",
        normalized_name="official expense",
        stable_key="expense_uncategorized",
    )
    legacy = _category(name="Legacy", normalized_name="sin_clasificar")
    with pytest.raises(Exception) as exc:
        mod._resolve_catalog_matches([official, legacy])
    message = str(exc.value)
    assert str(official.id) in message
    assert str(legacy.id) in message
    assert "expense_uncategorized" in message


def test_cross_criteria_conflict_and_multiple_legacy_candidates_abort():
    mod = _load_migration()
    stable_match = _category(name="Other", normalized_name="other", stable_key="expense_food")
    name_match = _category(name="Alimentación", normalized_name="alimentacion")
    with pytest.raises(Exception):
        mod._resolve_catalog_matches([stable_match, name_match])

    legacy_a = _category(name="sin_clasificar", normalized_name="legacy-a")
    legacy_b = _category(name="Other", normalized_name="sinclasificar")
    with pytest.raises(Exception):
        mod._resolve_catalog_matches([legacy_a, legacy_b])
