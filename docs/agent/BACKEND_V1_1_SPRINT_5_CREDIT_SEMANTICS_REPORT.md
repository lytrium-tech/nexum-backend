# Reporte de Cierre - Backend V1.1 - Sprint 5: Credit Semantics

## Resumen Ejecutivo
Sprint 5 se completó funcionalmente. Credit Core queda con una semántica financiera explícita basada en transacciones: la deuda vigente se calcula desde `credit_card_transactions`, no desde `credit_cards.current_debt`.

El resultado cumple el objetivo del roadmap: tarjetas de crédito comprensibles para el usuario, separando deuda actual, crédito disponible, deuda facturada, deuda no facturada, pago requerido y estimación del próximo pago.

## Decisiones Semánticas Cerradas
- Fuente de verdad V1.1: `credit_card_transactions` enlazadas a `financial_events`.
- `credit_cards.current_debt` permanece en BD, pero no se usa como fuente de lectura ni se sincroniza manualmente.
- `current_debt` expuesto por API es calculado.
- `available_credit = credit_limit - current_debt`.
- `payment_required = billed_debt`.
- `next_payment_estimate` se expone separado y marcado como estimado.
- `statement_balance = None` porque Nexum no persiste extractos cerrados.
- `management_fee`, `monthly_interest_rate`, `annual_interest_rate`, `network` y `franchise` son metadata; no generan cargos automáticos.

## Cambios Realizados
1. Modelos y migración:
   - Se agregaron `network` y `franchise` a tarjetas.
   - Se agregó `CreditCardInstallment` y tabla `credit_card_installments`.
   - Se creó `scripts/migrate_v11_sprint5.py` con migración aditiva y backfill de cuotas.

2. Contrato Credit V1.1:
   - `CreditCardRead` y `CreditCardStatusRead` exponen `current_debt`, `available_credit`, `payment_required`, `next_payment_estimate`, `statement_balance` y `data_quality`.
   - Se conservaron aliases legacy: `estimated_current_debt`, `estimated_available_credit`, `monthly_cc_payment`.
   - Se expone metadata financiera sin ejecutar intereses ni cuotas de manejo automáticas.

3. Servicio Credit:
   - Compras de tarjeta aumentan deuda y no reducen cash.
   - Pagos de tarjeta reducen cash y reducen deuda transaccional.
   - Sobrepagos se rechazan contra deuda calculada.
   - Cuotas se materializan como schedule de capital simple, sin intereses ni cargos implícitos.

4. Intelligence y conversación:
   - Snapshot usa `payment_required = billed_debt`.
   - `committed_outflows` no usa `next_payment_estimate` como obligación exigible.
   - Respuestas conversacionales de deuda distinguen deuda actual, pago requerido y estimación.

5. Operación y smokes:
   - Se agregó `scripts/smoke_credit_semantics_v11.py`.
   - Se actualizó `scripts/smoke_traceability.py` para seguir el flujo real de clarificación antes de confirmación y limpiar acciones pendientes al inicio.
   - `Dockerfile` copia `scripts/` para permitir migraciones/smokes dentro de imagen.

## Validaciones Ejecutadas
- `python -m uv run ruff check .`: All checks passed.
- `python -m uv run pytest tests/unit/test_credit.py tests/unit/test_intelligence.py -v`: 15 passed.
- `python -m uv run pytest tests/ -v`: 140 passed, 1 warning.
- `python -m uv run python scripts/smoke_credit_semantics_v11.py`: passed.
- `python -m uv run python scripts/smoke_credit_core.py`: passed.
- `python -m uv run python scripts/smoke_financial_truth_v11.py`: passed.
- `python -m uv run python scripts/smoke_obligations_v11.py`: passed.
- `python -m uv run python scripts/smoke_goals_consistency_v11.py`: passed.
- `python -m uv run python scripts/smoke_ledger_history.py`: passed.
- `python -m uv run python scripts/smoke_traceability.py`: passed.
- `python -m uv run python scripts/smoke_ownership.py`: passed.

## Auditoría Productiva Previa
La auditoría previa aprobada no encontró bloqueantes para la migración:
- `installments_total_invalid`: 0.
- `debt_negative_candidates`: 0.
- `payments_exceeding_purchases`: 0.
- `limit_lower_than_debt`: 0.
- `missing_credit_event_link`: 0.
- `credit_events_without_tx`: 0.
- `existing_installment_table`: 0.
- `network_column`: 0.
- `franchise_column`: 0.

La contradicción principal observada fue confirmada y resuelta semánticamente:
- `credit_cards.current_debt <> 0`: 0 tarjetas.
- `v_credit_card_debt.credit_card_debt <> 0`: 29 tarjetas, total `6030000.00`.

## Riesgos y Límites Explícitos
- No existe todavía statement persistido; por eso `statement_balance` se reporta como no disponible.
- El schedule de cuotas es una distribución de capital, no un motor de facturación.
- No se implementaron intereses, mora, pago mínimo bancario ni cuota de manejo automática.
- `credit_cards.current_debt` sigue existiendo por compatibilidad de esquema, pero queda obsoleto de facto.

## Estado Final
Sprint 5 queda funcionalmente cerrado a nivel local con migración, contrato, lógica, tests y regresión smoke aprobados.

Pendiente operativo antes de cierre productivo:
- Revisar diff final y preparar commit.
- Push a `main`.
- Ejecutar migración Sprint 5 en VPS.
- Redeploy productivo.
- Verificar `/health`, `/health/readiness` y estado Docker.

No se avanzó a Sprint 6.
