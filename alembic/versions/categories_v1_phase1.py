"""categories_v1_phase1

Revision ID: categories_v1_phase1
Revises: v1_7_phase_fx_rate_snapshot
Create Date: 2026-07-15 19:00:00.000000

"""

import re
import uuid

import sqlalchemy as sa

from alembic import context, op

# revision identifiers, used by Alembic.
revision = "categories_v1_phase1"
down_revision = "v1_7_phase_fx_rate_snapshot"
branch_labels = None
depends_on = None

GLOBAL_CATEGORIES = [
    ("expense_food", "Alimentación", "alimentacion", "expense", "food"),
    ("expense_transport", "Transporte", "transporte", "expense", "transport"),
    ("expense_housing", "Vivienda", "vivienda", "expense", "housing"),
    ("expense_health", "Salud", "salud", "expense", "health"),
    ("expense_education", "Educación", "educacion", "expense", "education"),
    ("expense_entertainment", "Entretenimiento", "entretenimiento", "expense", "entertainment"),
    ("expense_shopping", "Compras", "compras", "expense", "shopping"),
    ("expense_debt", "Deudas", "deudas", "expense", "debt"),
    ("expense_other", "Otros gastos", "otros gastos", "expense", "other"),
    ("expense_uncategorized", "Sin clasificar", "sin clasificar", "expense", "help_circle"),
    ("income_salary", "Salario", "salario", "income", "salary"),
    ("income_additional", "Ingresos extra", "ingresos extra", "income", "plus_circle"),
    ("income_returns", "Rendimientos", "rendimientos", "income", "trending_up"),
    ("income_gifts", "Regalos", "regalos", "income", "gift"),
    ("income_other", "Otros ingresos", "otros ingresos", "income", "other"),
    ("income_uncategorized", "Sin clasificar", "sin clasificar", "income", "help_circle"),
    ("system_credit_card", "Tarjeta Crédito", "tarjeta credito", "credit_card", "credit_card"),
    ("system_obligation", "Obligación", "obligacion", "obligation", "file_text"),
    ("system_goal", "Meta", "meta", "goal", "target"),
    ("system_transfer", "Transferencia", "transferencia", "transfer", "repeat"),
]

LEGACY_UNCATEGORIZED_NAMES = {"sin_clasificar", "sin clasificar", "sinclasificar"}
GLOBAL_PRIVATE_CONSTRAINT = (
    "((user_id IS NULL AND stable_key IS NOT NULL) OR (user_id IS NOT NULL AND stable_key IS NULL))"
)


def _normalize_string(val: str) -> str:
    if not val:
        return ""
    import unicodedata

    val = unicodedata.normalize("NFD", val).encode("ascii", "ignore").decode("utf-8")
    val = re.sub(r"[^\w\s]", "", val)
    val = re.sub(r"\s+", " ", val).strip().lower()
    return val


def _normalize_sql(sql: str) -> str:
    if not sql:
        return ""
    expression = sql.lower()
    expression = re.sub(r'"([a-z_][a-z0-9_]*)"', r"\1", expression)
    expression = re.sub(r"\b(and|or|is|not|null)\b", r" \1 ", expression)
    expression = re.sub(r"\s+", " ", expression).strip()
    return _strip_redundant_outer_parentheses(expression)


def _strip_redundant_outer_parentheses(expression: str) -> str:
    result = expression.strip()
    while result.startswith("(") and result.endswith(")"):
        depth = 0
        encloses_whole_expression = True
        for index, char in enumerate(result):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    return result
            if depth == 0 and index != len(result) - 1:
                encloses_whole_expression = False
                break
        if depth != 0 or not encloses_whole_expression:
            break
        result = result[1:-1].strip()
    return result


def _split_top_level(expression: str, operator: str) -> list[str]:
    token = f" {operator} "
    parts = []
    start = 0
    depth = 0
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return []
        elif depth == 0 and expression.startswith(token, index):
            parts.append(expression[start:index].strip())
            index += len(token)
            start = index
            continue
        index += 1
    if depth != 0:
        return []
    parts.append(expression[start:].strip())
    return parts


def _is_valid_global_private_constraint(sql: str) -> bool:
    expression = _normalize_sql(sql)
    branches = _split_top_level(expression, "or")
    if len(branches) != 2:
        return False

    normalized_branches = []
    for branch in branches:
        predicates = _split_top_level(_strip_redundant_outer_parentheses(branch), "and")
        if len(predicates) != 2:
            return False
        normalized_branches.append(frozenset(_normalize_sql(predicate) for predicate in predicates))

    expected = {
        frozenset({"user_id is null", "stable_key is not null"}),
        frozenset({"user_id is not null", "stable_key is null"}),
    }
    return set(normalized_branches) == expected


def _validate_index_definition(
    index: dict,
    *,
    expected_name: str,
    expected_columns: list[str],
    expected_predicate: str,
) -> None:
    if index.get("name") != expected_name:
        return
    if not index.get("unique"):
        raise Exception(f"El índice '{expected_name}' preexistente no es unique.")
    if index.get("column_names") != expected_columns:
        raise Exception(
            f"El índice '{expected_name}' no cubre las columnas esperadas: {expected_columns}."
        )
    options = index.get("dialect_options", {})
    predicate = str(options.get("postgresql_where", options.get("sqlite_where", "")))
    if _normalize_sql(predicate) != _normalize_sql(expected_predicate):
        raise Exception(
            f"El índice '{expected_name}' tiene un predicado incompatible. Found: {predicate}"
        )


def _validate_constraint_definition(constraint: dict) -> None:
    if constraint.get("name") != "chk_categories_global_or_private":
        return
    sqltext = str(constraint.get("sqltext", ""))
    if not _is_valid_global_private_constraint(sqltext):
        raise Exception(
            "El constraint 'chk_categories_global_or_private' preexistente es inválido. "
            f"Found: {sqltext}"
        )


def _is_legacy_expense_uncategorized(category) -> bool:
    if category.user_id is not None or category.type != "expense":
        return False
    name = _normalize_string(category.name)
    normalized_name = _normalize_string(category.normalized_name)
    return name in LEGACY_UNCATEGORIZED_NAMES or normalized_name in LEGACY_UNCATEGORIZED_NAMES


def _describe_category(category) -> str:
    return (
        f"id={category.id}, stable_key={category.stable_key!r}, type={category.type!r}, "
        f"name={category.name!r}, normalized_name={category.normalized_name!r}"
    )


def _resolve_catalog_matches(all_categories) -> dict[str, object]:
    globals_by_sk = {}
    globals_by_type_norm = {}
    for category in all_categories:
        if category.user_id is not None:
            continue
        if category.stable_key is not None:
            if category.stable_key in globals_by_sk:
                raise Exception(
                    f"Precheck fallido: Stable key global duplicada: {category.stable_key!r}."
                )
            globals_by_sk[category.stable_key] = category
        globals_by_type_norm.setdefault((category.type, category.normalized_name), []).append(
            category
        )

    matched_ids = {}
    for canon_sk, _name, canon_norm, canon_type, _icon in GLOBAL_CATEGORIES:
        match_a = globals_by_sk.get(canon_sk)
        match_b_list = globals_by_type_norm.get((canon_type, canon_norm), [])
        if len(match_b_list) > 1:
            raise Exception(
                "Precheck fallido: Múltiples categorías globales candidatas para "
                f"{(canon_type, canon_norm)}."
            )
        match_b = match_b_list[0] if match_b_list else None
        if match_a and match_b and match_a.id != match_b.id:
            raise Exception(
                f"Precheck fallido: Criterio A (stable_key={canon_sk}) y B "
                f"({(canon_type, canon_norm)}) apuntan a filas distintas: "
                f"A[{_describe_category(match_a)}], B[{_describe_category(match_b)}]."
            )
        target = match_a or match_b
        if target:
            if match_a and match_a.type != canon_type:
                raise Exception(
                    f"Precheck fallido: La categoría con stable_key {canon_sk} tiene un "
                    f"type distinto al canónico: {_describe_category(match_a)}."
                )
            matched_ids[canon_sk] = target.id

    legacy_candidates = [
        category for category in all_categories if _is_legacy_expense_uncategorized(category)
    ]
    official_id = matched_ids.get("expense_uncategorized")
    conflicting_legacy = [
        category
        for category in legacy_candidates
        if official_id is not None and category.id != official_id
    ]
    if conflicting_legacy:
        official = next(category for category in all_categories if category.id == official_id)
        conflicts = "; ".join(_describe_category(category) for category in conflicting_legacy)
        raise Exception(
            "Precheck fallido: expense_uncategorized y una fila legacy apuntan a IDs "
            f"distintos. Oficial[{_describe_category(official)}]; Legacy[{conflicts}]."
        )
    if official_id is None:
        if len(legacy_candidates) > 1:
            details = "; ".join(_describe_category(category) for category in legacy_candidates)
            raise Exception(
                "Precheck fallido: Múltiples candidatos legacy para 'Sin clasificar' "
                f"expense: {details}."
            )
        if legacy_candidates:
            matched_ids["expense_uncategorized"] = legacy_candidates[0].id
    return matched_ids


def upgrade():
    is_offline = context.is_offline_mode()

    if is_offline:
        raise Exception(
            "Esta migración requiere ejecución online debido a prechecks y reconciliación de datos."
        )

    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = [c["name"] for c in insp.get_columns("categories")]

    has_stable_key = "stable_key" in columns
    has_icon_key = "icon_key" in columns

    # Validate existing columns
    if has_stable_key:
        stable_col = next(c for c in insp.get_columns("categories") if c["name"] == "stable_key")
        if not isinstance(stable_col["type"], (sa.String, sa.Text)):
            raise Exception(
                "La columna preexistente 'stable_key' no es de tipo compatible (String/Text)."
            )
        # In SQLite nullable might be True/False, ensure it is True
        if not stable_col.get("nullable", True):
            raise Exception(
                "La columna preexistente 'stable_key' debe permitir nulos (nullable=True)."
            )

    if has_icon_key:
        icon_col = next(c for c in insp.get_columns("categories") if c["name"] == "icon_key")
        if not isinstance(icon_col["type"], (sa.String, sa.Text)):
            raise Exception(
                "La columna preexistente 'icon_key' no es de tipo compatible (String/Text)."
            )
        if not icon_col.get("nullable", True):
            raise Exception(
                "La columna preexistente 'icon_key' debe permitir nulos (nullable=True)."
            )

    # Validate existing constraints and indexes
    existing_indexes = insp.get_indexes("categories")
    for idx in existing_indexes:
        _validate_index_definition(
            idx,
            expected_name="idx_categories_stable_key_unique",
            expected_columns=["stable_key"],
            expected_predicate="stable_key IS NOT NULL",
        )
        _validate_index_definition(
            idx,
            expected_name="idx_categories_global_type_norm_name_unique",
            expected_columns=["type", "normalized_name"],
            expected_predicate="user_id IS NULL",
        )

    constraints = insp.get_check_constraints("categories")
    for chk in constraints:
        _validate_constraint_definition(chk)

    # Data prechecks and gathering
    if not has_stable_key:
        op.add_column("categories", sa.Column("stable_key", sa.Text(), nullable=True))
    if not has_icon_key:
        op.add_column("categories", sa.Column("icon_key", sa.Text(), nullable=True))

    all_categories = bind.execute(
        sa.text("SELECT id, user_id, type, name, normalized_name, stable_key FROM categories")
    ).fetchall()

    # Check for private cats with stable_key
    private_with_sk = [
        c for c in all_categories if c.user_id is not None and c.stable_key is not None
    ]
    if private_with_sk:
        raise Exception("Precheck fallido: Existen categorías privadas con stable_key asignada.")

    matched_ids = _resolve_catalog_matches(all_categories)

    # Seeding convergente
    for canon_sk, canon_name, canon_norm, canon_type, canon_icon in GLOBAL_CATEGORIES:
        target_id = matched_ids.get(canon_sk)
        if target_id:
            bind.execute(
                sa.text("""
                    UPDATE categories
                    SET stable_key = :sk, icon_key = :ik, name = :name, normalized_name = :norm, type = :type, is_active = true
                    WHERE id = :id
                """),
                {
                    "sk": canon_sk,
                    "ik": canon_icon,
                    "name": canon_name,
                    "norm": canon_norm,
                    "type": canon_type,
                    "id": target_id,
                },
            )
        else:
            bind.execute(
                sa.text("""
                    INSERT INTO categories (id, user_id, name, normalized_name, type, is_active, stable_key, icon_key)
                    VALUES (:id, NULL, :name, :norm, :type, true, :sk, :ik)
                """),
                {
                    "id": str(uuid.uuid4()),
                    "name": canon_name,
                    "norm": canon_norm,
                    "type": canon_type,
                    "sk": canon_sk,
                    "ik": canon_icon,
                },
            )

    # Indexes and constraints
    existing_idx_names = [ix["name"] for ix in existing_indexes]
    if "idx_categories_stable_key_unique" not in existing_idx_names:
        op.create_index(
            "idx_categories_stable_key_unique",
            "categories",
            ["stable_key"],
            unique=True,
            postgresql_where=sa.text("stable_key IS NOT NULL"),
            sqlite_where=sa.text("stable_key IS NOT NULL"),
        )
    if "idx_categories_global_type_norm_name_unique" not in existing_idx_names:
        op.create_index(
            "idx_categories_global_type_norm_name_unique",
            "categories",
            ["type", "normalized_name"],
            unique=True,
            postgresql_where=sa.text("user_id IS NULL"),
            sqlite_where=sa.text("user_id IS NULL"),
        )

    constraint_names = [c["name"] for c in constraints]
    if "chk_categories_global_or_private" not in constraint_names:
        op.create_check_constraint(
            "chk_categories_global_or_private",
            "categories",
            "((user_id IS NULL AND stable_key IS NOT NULL) OR (user_id IS NOT NULL AND stable_key IS NULL))",
        )


def downgrade():
    """Conserva íntegramente el estado aditivo de Categorías V1 Fase 1.

    Las columnas, índices y constraint compatibles pudieron existir antes de esta
    revisión por el antiguo script manual. Alembic no puede determinar de forma
    segura la propiedad de esos objetos durante downgrade, por lo que no elimina
    esquema, catálogo ni datos y evita destruir infraestructura preexistente.
    """
    return None
