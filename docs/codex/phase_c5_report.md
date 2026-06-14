# Financial Snapshot MVP Report — Fase C.5

## 1. Estado previo

C.4 Account Transfers estaba presente en código y base de datos: endpoints `/api/v1/transfers`, tabla `public.transfers`, relación `financial_events.transfer_id`, eventos `transfer_out` y `transfer_in`, y exclusión de transferencias del net cashflow de Ledger.

El endpoint `/api/v1/intelligence/snapshot` ya existía, pero el contrato anterior no estaba alineado con el modelo frontend-ready definido para C.5.

## 2. Endpoints implementados

Se consolidaron endpoints existentes sin duplicar rutas:

- `GET /api/v1/intelligence/snapshot`
- `GET /api/v1/intelligence/cashflow`
- `GET /api/v1/intelligence/debt`
- `GET /api/v1/intelligence/goals`
- `GET /api/v1/intelligence/obligations`

## 3. Modelo de snapshot

El contrato principal quedó estructurado para Home MVP:

```json
{
  "period": { "month": "2026-06", "timezone": "America/Bogota" },
  "cash": { "total_balance": "1900000.00", "active_accounts_count": 2 },
  "cashflow": { "income": "3000000.00", "expenses": "200000.00", "net_cashflow": "2800000.00" },
  "debt": { "credit_card_total_debt": "300000.00", "billed_debt": "0.00", "unbilled_debt": "300000.00" },
  "goals": { "active_goals_count": 1, "total_target": "5000000.00", "total_saved": "400000.00" },
  "obligations": { "pending_count": 1, "pending_amount": "900000.00" },
  "transfers": { "monthly_transfer_volume": "400000.00" },
  "recent_activity": []
}
```

## 4. Cash

Cash usa cuentas activas del usuario desde `accounts`, sumando `balance` y contando cuentas activas.

## 5. Cashflow

Cashflow mensual del snapshot usa `financial_events` para el periodo actual y agrega únicamente:

- `income`
- `expense`

`net_cashflow = income - expenses`.

## 6. Debt

Debt ya no depende de una suma simplificada de `credit_cards.current_debt`.

El snapshot usa `CreditCardService.get_credit_summary()`, por lo que respeta el Credit Core C.1:

- `total_debt`
- `billed_debt`
- `unbilled_debt`

## 7. Goals

Goals agrega metas activas desde `goals`:

- cantidad de metas activas
- objetivo total
- ahorro/progreso acumulado total

Los aportes siguen impactando `goals.current_amount`, `goal_contributions` y `financial_events` según el flujo existente.

## 8. Obligations

Obligations calcula pendientes del periodo actual usando el modelo vigente:

- obligación activa sin pago registrado en el periodo actual => pendiente
- obligación con pago registrado en el periodo actual => no pendiente

## 9. Transfers treatment

Transfers se reporta como volumen mensual separado en `transfers.monthly_transfer_volume`.

Las transferencias no cuentan como `income`, `expenses` ni alteran `net_cashflow`.

## 10. Recent activity

`recent_activity` devuelve los últimos eventos desde `financial_events`, ordenados por `occurred_at DESC`, limitado a 5 registros.

Incluye eventos como income, expense, goal contribution, obligation payment, credit card purchase/payment y transfer in/out.

## 11. Timezone

El snapshot calcula el periodo mensual con `America/Bogota`.

Limitación controlada: algunas vistas existentes de inteligencia usan `period` calculado en DB con `America/Bogota`, mientras el nuevo snapshot usa rangos `occurred_at >= month_start` y `< next_month_start` generados desde Python con timezone `America/Bogota`. Esto queda alineado conceptualmente para MVP, pero C.6 debería revisar consistencia total entre views, `period` materializado y rangos timestamptz.

## 12. Frontend readiness

El snapshot queda listo para:

- Home summary cards
- Cash card
- Debt card
- Goals card
- Obligations card
- Recent activity
- Monthly cashflow card

## 13. Ownership

Todas las consultas reciben `current_profile.id` desde `CurrentUserProfile`.

El smoke C.5 valida que un segundo usuario no ve cash, cashflow ni transfers del usuario principal.

Advertencia de seguridad detectada en Supabase: `public.transfers` tiene RLS deshabilitado. El backend aplica ownership en aplicación, pero si clientes Supabase acceden directo con anon/authenticated, esta tabla queda expuesta. No se modificó schema en C.5 por instrucción de no tocar schema sin necesidad funcional.

## 14. Traceability

C.5 no modifica `raw_message`, `source_message_id`, `trace_id`, `pending_actions`, `messages` ni `ai_runs`.

El snapshot solo lee fuentes consolidadas y no altera trazabilidad B.3.

## 15. Tests

Validación ejecutada:

```bash
python -m uv run pytest tests/ -v
```

Resultado:

```text
131 passed, 1 warning
```

Tests C.5 añadidos/actualizados:

- snapshot con usuario sin datos
- snapshot con cash/accounts
- snapshot con income/expense
- snapshot con deuda de crédito billed/unbilled desde Credit Core
- snapshot con goals
- snapshot con obligations
- snapshot con transfers
- transfers no alteran income/expense/net_cashflow
- no uso de `public.transactions` en intelligence repository

Lint C.5 ejecutado:

```bash
python -m uv run ruff check app/intelligence tests/unit/test_intelligence.py scripts/smoke_financial_snapshot.py
```

Resultado:

```text
All checks passed
```

`ruff check .` sigue fallando por deuda preexistente fuera de C.5: imports tardíos/desordenados en módulos previos, scripts antiguos y `check_db.py` con contenido no UTF-8.

## 16. Smokes

Ejecutados:

```bash
python -m uv run python scripts/cleanup_dev.py
python -m uv run python scripts/seed_dev.py
python -m uv run python scripts/smoke_financial_snapshot.py
python -m uv run python scripts/smoke_cash.py
python -m uv run python scripts/smoke_credit_core.py
python -m uv run python scripts/smoke_accounts_categories.py
python -m uv run python scripts/smoke_ledger_history.py
python -m uv run python scripts/smoke_transfers.py
python -m uv run python scripts/smoke_conversational_cash.py
python -m uv run python scripts/smoke_traceability.py
python -m uv run python scripts/smoke_ownership.py
```

Resultado:

- `smoke_financial_snapshot.py`: PASSED contra instancia actual en `NEXUM_BASE_URL=http://localhost:8001`.
- `smoke_cash.py`: PASSED.
- `smoke_credit_core.py`: PASSED.
- `smoke_accounts_categories.py`: PASSED.
- `smoke_ledger_history.py`: PASSED.
- `smoke_transfers.py`: PASSED.
- `smoke_conversational_cash.py`: PASSED.
- `smoke_traceability.py`: PASSED.
- `smoke_ownership.py`: PASSED.

Nota: `localhost:8000` tenía una instancia antigua durante la validación inicial y devolvía el contrato anterior de snapshot. Se levantó una instancia temporal del código actual en `8001` para validar C.5 correctamente.

## 17. Migraciones

No se crearon migraciones.

No se modificó schema.

No se tocó `transactions`.

## 18. Commit

Commit funcional creado y pusheado:

```text
1417b14 feat: implement financial snapshot mvp
```

## 19. Deploy

Deploy ejecutado en VPS con el flujo operativo:

```bash
ssh lytrium-vps
cd /opt/nexum-backend
git pull origin main
docker compose build
docker compose up -d
docker compose ps
```

El contenedor `nexum_backend_api` fue recreado correctamente.

## 20. Health/readiness

Health/readiness productivos validados:

```text
GET https://api.nexum.lytrium.tech/health -> {"status":"ok","service":"nexum-backend"}
GET https://api.nexum.lytrium.tech/health/readiness -> {"status":"ok","service":"nexum-backend"}
```

## 21. Limitaciones conocidas

- `public.transfers` tiene RLS deshabilitado en Supabase. Requiere hardening antes de beta pública.
- `ruff check .` falla por deuda previa fuera de C.5.
- Algunos smokes antiguos siguen hardcodeados a `localhost:8000`.
- Consistencia timezone entre rangos Python y vistas SQL debe revisarse en C.6.

## 22. Recomendación para C.6

Proceder a C.6 Backend MVP Final Regression solo después de aprobación explícita.

Recomendaciones para C.6:

- limpiar deuda de lint global sin mezclar cambios funcionales
- alinear instancia local/deploy para evitar contratos antiguos en `localhost:8000`
- revisar RLS de `public.transfers`
- validar health/readiness en VPS tras deploy
- generar snapshot final de OpenAPI para handoff frontend
