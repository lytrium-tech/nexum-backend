# Nexum V1.5 - Credit Page 500 After Summary Hotfix

## Problema

Después de desplegar el commit `696b8ea`, la página principal de crédito (`/app/credit`) y los resúmenes de tarjeta comenzaron a devolver error HTTP 500, impidiendo la visualización de los datos.

## Diagnóstico

Se recolectó el traceback real desde la VPS (`docker compose logs api`), el cual expuso la raíz del problema:

```
sqlalchemy.exc.ProgrammingError: (sqlalchemy.dialects.postgresql.asyncpg.ProgrammingError) <class 'asyncpg.exceptions.PostgresSyntaxError'>: syntax error at or near ":"
```

La consulta SQL agregada en `get_card_status_data` introducía la expresión:
`to_char(:cycle_end::date, 'YYYY-MM')`

El uso de `::date` junto al marcador de parámetro nombrado `:cycle_end` entra en conflicto con el analizador de parámetros `text()` de SQLAlchemy. SQLAlchemy intenta interceptar los dos puntos como indicadores de parámetro, corrompiendo la consulta que finalmente se envía a Postgres.

## Solución Aplicada

Se corrigió la sintaxis conflictiva en `app/credit/repository.py`, reemplazando el casteo directo `::date` por la función estándar SQL `CAST()`:

```sql
to_char(CAST(:cycle_end AS DATE), 'YYYY-MM')
```

Esto evita la confusión del analizador de SQLAlchemy y envía correctamente el parámetro nombrado hacia la base de datos subyacente.

## Validación
- Se agregó el test `test_calculate_card_status_pay_early` y `test_calculate_card_status_no_debt` en `tests/unit/test_credit_summary_qa.py` para asegurar que el motor interno de resumen no rompa las llamadas y devuelva correctamente los cálculos de deuda considerando `paid_amount` (simulando la respuesta de la base de datos).
- `pytest tests/ -v`: 228/228 passed.
- `ruff check .`: passed.
- No se mutó `principal_amount` ni se alteró ninguna otra regla financiera.
