# Backend V1.1 Sprint 5 — Credit Semantics Audit

## 1. Executive Summary
Sprint 5 debe resolver una contradicción estructural del dominio Credit: hoy no existe una única fuente de verdad explícita y estable para deuda de tarjeta.

Evidencia principal:
- `credit_cards.current_debt` existe en BD y modelo, pero no se sincroniza en `app/credit/service.py`.
- Las APIs operativas usan deuda calculada desde `credit_card_transactions` vía `v_credit_card_debt` o agregados directos.
- Producción confirma la divergencia: `credit_cards.current_debt` suma `0`, mientras `v_credit_card_debt` reporta deuda en 29 tarjetas por `6030000.00`.
- `statement_balance` no existe como columna, schema, endpoint ni cálculo explícito.
- `next_payment_estimate` no existe en `CreditCardStatusRead`, pero `app/intelligence/service.py` lo intenta leer cuando `billed_debt <= 0`, generando riesgo de error en snapshot.
- `payment_required` hoy significa `billed_debt` si existe; si no existe, intenta usar un campo inexistente.

Conclusión: Sprint 5 debe primero declarar que la fuente de verdad V1.1 es transaccional (`credit_card_transactions` + eventos ledger enlazados), no `credit_cards.current_debt`, y después exponer campos semánticos claros sin implementar intereses complejos.

## 2. Current Database Schema
Tablas productivas auditadas en VPS con consultas de solo lectura a `information_schema`.

`credit_cards` contiene:
- `id uuid NOT NULL DEFAULT gen_random_uuid()`
- `user_id uuid`
- `name text`
- `bank text`
- `credit_limit numeric`
- `current_debt numeric DEFAULT 0`
- `cutoff_day integer`
- `due_day integer`
- `management_fee numeric DEFAULT 0`
- `monthly_interest_rate numeric DEFAULT 0`
- `annual_interest_rate numeric DEFAULT 0`
- `currency text NOT NULL DEFAULT 'COP'`
- `is_active boolean NOT NULL DEFAULT true`
- `created_at`, `updated_at`

`credit_card_transactions` contiene:
- `type text` con valores permitidos `purchase`, `payment`, `fee`, `interest`, `adjustment`.
- `amount numeric CHECK amount > 0`.
- `installments_total integer DEFAULT 1`.
- `installments_paid integer DEFAULT 0`.
- `monthly_amount numeric`.
- `interest_amount numeric DEFAULT 0`.
- `total_with_interest numeric`.
- `event_id uuid` FK a `financial_events`.
- `account_id uuid`, `category_id uuid`.
- `occurred_at timestamp with time zone NOT NULL DEFAULT now()`.
- `period text`, `source text`, `metadata jsonb`.

`financial_events` permite:
- `credit_card_purchase`.
- `credit_card_payment`.
- `direction` permite `neutral` y `outflow`.

Vistas productivas relevantes:
- `v_credit_card_debt` calcula `credit_card_debt = GREATEST(purchases_total - payments_total, 0)`.
- `v_credit_card_debt` calcula `monthly_cc_payment` como `LEAST(monthly_purchases_total, current debt)`.
- `v_cashflow_current_month` separa `credit_card_payment` como `debt_service`.
- `v_consumption_summary_current_month` cuenta `credit_card_purchase` como `credit_card_consumption_committed` y no como cash outflow.

Datos productivos observados:
- `credit_card_payment/outflow`: 25 eventos, total `2720000.00`.
- `credit_card_purchase/neutral`: 51 eventos, total `8750000.00`.
- `credit_cards.current_debt <> 0`: 0 tarjetas.
- `v_credit_card_debt.credit_card_debt <> 0`: 29 tarjetas, total `6030000.00`.
- No se observaron `financial_events` de crédito sin `credit_card_transactions` enlazada.
- No se observaron `credit_card_transactions` sin `event_id`.

## 3. Current API Contract
Endpoints actuales en `app/credit/router.py`:
- `POST /api/v1/credit/cards` crea tarjeta y devuelve `CreditCardRead`.
- `GET /api/v1/credit/cards` lista tarjetas enriquecidas.
- `GET /api/v1/credit/cards/{card_id}` devuelve tarjeta enriquecida.
- `PATCH /api/v1/credit/cards/{card_id}` actualiza tarjeta.
- `DELETE /api/v1/credit/cards/{card_id}` desactiva tarjeta.
- `POST /api/v1/credit/cards/{card_id}/purchases` registra compra.
- `POST /api/v1/credit/cards/{card_id}/payments` registra pago.
- `GET /api/v1/credit/summary` devuelve resumen.
- `GET /api/v1/credit/cards/{card_id}/status` devuelve status.

Schemas actuales:
- `CreditCardRead` expone `estimated_current_debt`, `monthly_cc_payment`, `estimated_available_credit`.
- `CreditCardStatusRead` expone `total_debt`, `billed_debt`, `unbilled_debt`, `available_credit`, `monthly_cc_payment`, `next_payment_due_date`.
- No existe `statement_balance`.
- No existe `payment_required` en el contrato de Credit.
- No existe `next_payment_estimate` en el contrato de Credit.
- No se exponen `management_fee`, `monthly_interest_rate` ni `annual_interest_rate` en create/update/read.

## 4. Current Debt Source of Truth
Respuesta corta: no hay una fuente única formal, pero la fuente efectiva usada por API es transaccional.

Evidencia:
- `app/credit/models.py` define `CreditCard.current_debt`.
- `CreditCardService._enrich_card()` ignora `card.current_debt` y usa `repo.get_card_debt(card.id)` desde `v_credit_card_debt`.
- `CreditCardService.create_purchase()` calcula deuda inicial con `repo.get_card_debt()`, luego retorna `estimated_debt + payload.amount`; no escribe `card.current_debt`.
- `CreditCardService.create_payment()` calcula deuda inicial con `repo.get_card_debt()`, luego retorna `estimated_debt - payload.amount`; no escribe `card.current_debt`.
- `CreditCardService.get_card_status()` no usa la vista para total debt; usa `repo.get_card_status_data()` sobre `credit_card_transactions` y separa billed/unbilled por fecha.
- `IntelligenceService.get_snapshot()` usa `CreditCardService.get_credit_summary()`.

Contradicción:
- `IntelligenceRepository.get_debt_metrics()` todavía suma `credit_cards.current_debt`, pero el snapshot actual no usa ese método.
- Producción demuestra que `current_debt` está obsoleto de facto.

Decisión recomendada V1.1:
- Fuente de verdad: `credit_card_transactions` enlazadas a `financial_events`.
- `credit_cards.current_debt`: deprecar en lectura y dejar de usar; migración futura opcional para eliminarlo o documentarlo como cache no confiable.

## 5. Purchases Behavior
Flujo actual de compra en `CreditCardService.create_purchase()`:
- Bloquea tarjeta con `get_by_id_for_update()`.
- Calcula deuda desde `v_credit_card_debt`.
- Calcula disponible como `credit_limit - estimated_debt`.
- Rechaza si `payload.amount > estimated_available`.
- Inserta `financial_events` con `event_type='credit_card_purchase'`, `direction='neutral'` y metadata `credit_card_id`.
- Inserta `credit_card_transactions` con `type='purchase'`, `amount`, `installments_total`, `monthly_amount = amount / installments_total`, `event_id`.
- No reduce saldo de cuenta.
- No actualiza `credit_cards.current_debt`.

Principio cumplido:
- Una compra con tarjeta aumenta deuda y no reduce cash inmediatamente.

Riesgo:
- En retry idempotente, si existe el evento pero falta la transacción, retorna `transaction_id=None` y no repara la transacción. Producción no muestra casos así, pero el riesgo existe.

## 6. Payments Behavior
Flujo actual de pago en `CreditCardService.create_payment()`:
- Bloquea cuenta con `AccountRepository.get_by_id_for_update()`.
- Valida ownership, cuenta activa y fondos suficientes.
- Bloquea tarjeta con `get_by_id_for_update()`.
- Calcula deuda desde `v_credit_card_debt`.
- Rechaza sobrepago si `payload.amount > estimated_debt`.
- Inserta `financial_events` con `event_type='credit_card_payment'`, `direction='outflow'`, `account_id` y metadata `credit_card_id`.
- Resta `payload.amount` de `account.balance`.
- Inserta `credit_card_transactions` con `type='payment'`, `account_id`, `event_id`.
- No actualiza `credit_cards.current_debt`.

Principios cumplidos:
- Un pago de tarjeta reduce cash.
- Un pago de tarjeta reduce deuda transaccional.
- Se bloquea sobrepago contra deuda calculada.

Riesgos:
- La reducción de deuda depende de que la transacción `payment` se cree correctamente después del evento ledger.
- Si ocurre un evento idempotente sin transacción asociada, no se repara automáticamente.
- `credit_cards.current_debt` queda desincronizado.

## 7. Billed vs Unbilled Debt
Cálculo actual en `CreditCardService.get_card_status()`:
- Usa `calculate_credit_card_dates(date.today(), cutoff_day, due_day)`.
- Define `last_cutoff = cycle_start - 1 day`.
- `get_card_status_data()` clasifica compras como billed si `DATE(occurred_at AT TIME ZONE 'UTC') <= last_cutoff`.
- Clasifica compras como unbilled si `DATE(occurred_at AT TIME ZONE 'UTC') > last_cutoff`.
- Suma todos los pagos históricos como `total_payments`.
- Aplica pagos primero a `billed_purchases`.
- Si pagos exceden billed, reduce `unbilled_purchases`.

Riesgos:
- El corte se basa en `UTC`, no `America/Bogota`.
- No existe statement/ciclo persistido; billed/unbilled se recalcula dinámicamente.
- Pagos históricos siempre se aplican al acumulado, sin statement cerrado ni pago por ciclo.
- No hay modelo de mora, pago mínimo ni fecha real de extracto.

## 8. payment_required
Hoy `payment_required` existe en `SnapshotTruth`, no en Credit.

Cálculo actual en `app/intelligence/service.py`:
- `payment_required = billed_debt` si `billed_debt > 0`.
- Si `billed_debt <= 0`, intenta sumar `card.next_payment_estimate`.

Contradicción crítica:
- `CreditCardStatusRead` no tiene `next_payment_estimate`.
- Por tanto, si existe tarjeta con deuda no facturada y `billed_debt == 0`, el snapshot puede fallar con `AttributeError` al intentar leer un campo inexistente.

Por qué puede estar en cero cuando existe deuda:
- Si no hay deuda facturada (`billed_debt = 0`) y no se usa un estimado válido, el compromiso debería ser 0 o un estimado explícito.
- La semántica actual intenta estimar, pero el campo no existe.
- En vistas legacy, `monthly_cc_payment` sí existe como aproximación, pero `get_snapshot()` ya usa `CreditCardService`, no la vista legacy directamente.

Decisión recomendada V1.1:
- Definir `payment_required = billed_debt`.
- Si `billed_debt == 0`, `payment_required = 0` en V1.1, salvo aprobación explícita de usar `next_payment_estimate`.
- Exponer `next_payment_estimate` como campo separado y marcado `estimated`, no mezclarlo como deuda exigible.

## 9. next_payment_estimate
No existe hoy como campo real en Credit.

Lo más cercano:
- `monthly_cc_payment` en `v_credit_card_debt`.
- `monthly_cc_payment` en `CreditCardRead` y `CreditCardStatusRead`.

Problema:
- `monthly_cc_payment` no equivale necesariamente a pago requerido.
- En compras a cuotas, suma cuotas mensuales estimadas de compras activas.
- En una compra de 6 cuotas, producción muestra `monthly_amount = amount / installments_total`.
- No hay schedule real ni cuotas marcadas como causadas/pagadas por periodo.

Decisión recomendada V1.1:
- Renombrar semánticamente `monthly_cc_payment` a `next_payment_estimate` solo si se documenta como estimación.
- No usarlo como `payment_required` salvo aprobación del founder.
- Mantener `payment_required` separado de `next_payment_estimate`.

## 10. Installments
Estado actual:
- `CreditCardPurchaseCreate.installments_total` existe.
- `CreditCardTransaction.installments_total` existe.
- `CreditCardTransaction.monthly_amount = amount / installments_total`.
- `CreditCardTransaction.installments_paid` existe, pero no se actualiza en pagos.
- No existe tabla de schedule de cuotas.
- No existe `installment_due_date`, `installment_number`, `period`, `status` por cuota.

Producción:
- Compras a 1 cuota: 43, total `6350000.00`.
- Compras a 6 cuotas: 8, total `2400000.00`, monthly sum `400000.00`.

Conclusión:
- Actualmente hay campos agregados de cuotas, no schedule real.
- Los pagos reducen deuda total, no cuotas específicas.

Decisión recomendada V1.1:
- No implementar schedule complejo todavía si no hay regla aprobada.
- Documentar `installments_total` y `monthly_amount` como estimación simple.
- Si se necesita verdad financiera por cuotas, crear schedule explícito en una migración aprobada.

## 11. Management Fee and Rates
Estado actual:
- `credit_cards.management_fee` existe en BD y modelo.
- `credit_cards.monthly_interest_rate` existe en BD y modelo.
- `credit_cards.annual_interest_rate` existe en BD y modelo.
- Hay constraints para tasas no negativas.
- No están en `CreditCardCreate`, `CreditCardUpdate`, `CreditCardRead` ni `CreditCardStatusRead`.
- No hay lógica que genere transacciones `fee` o `interest`.

Producción:
- `management_fee_total = 31000.00`.
- `max_monthly_rate = 2.000000`.
- `max_annual_rate = 18.000000`.

Riesgo:
- Hay datos productivos no expuestos ni usados.
- Si el frontend espera ver tasas/comisiones, hoy no puede hacerlo vía API.

Decisión recomendada V1.1:
- Exponer estos campos como metadata informativa.
- No calcular intereses automáticos ni cargos de manejo hasta aprobar una regla financiera.
- No crear transacciones `fee`/`interest` automáticas en Sprint 5 sin decisión explícita.

## 12. Snapshot Truth Integration
Snapshot actual:
- `debt.credit_card_total_debt = credit_summary.total_debt`.
- `debt.billed_debt = sum(card.billed_debt)`.
- `debt.unbilled_debt = sum(card.unbilled_debt)`.
- `truth.payment_required` se agrega a `truth.committed_outflows`.

Impacto actual en `committed_outflows`:
- Incluye `payment_required`.
- Si `payment_required = billed_debt`, solo deuda facturada afecta dinero libre.
- Si se intentara usar `next_payment_estimate`, hoy falla porque el campo no existe.

Riesgo:
- La ambigüedad de `payment_required` puede subestimar o romper `committed_outflows`.
- `free_money` depende directamente de esta decisión.

Decisión recomendada V1.1:
- `committed_outflows` debe incluir solo `payment_required` definido como obligación exigible del ciclo.
- `unbilled_debt` debe mostrarse como deuda total, pero no necesariamente comprometer dinero libre del periodo.
- `next_payment_estimate` debe vivir en `data_quality`/campo separado si se usa.

## 13. Ledger Compatibility
Ledger actual:
- Compra de tarjeta: `financial_events.event_type='credit_card_purchase'`, `direction='neutral'`.
- Pago de tarjeta: `financial_events.event_type='credit_card_payment'`, `direction='outflow'`.
- `LedgerRepository.get_summary()` suma compras de tarjeta en `total_credit_card_purchases`, pagos en `total_credit_card_payments`.
- `net_cashflow` resta solo eventos `direction='outflow'`, por lo que compras con tarjeta no reducen cash.
- `v_cashflow_current_month` separa pagos de tarjeta como `debt_service`.
- `v_consumption_summary_current_month` suma compras con tarjeta como consumo comprometido, no cash outflow.

Principios cumplidos:
- Credit card purchase no es expense de consumo en Ledger Summary.
- Credit card payment no se cuenta como expense de consumo.
- Credit card payment sí reduce liquidez vía `debt_service`/outflow.

Riesgo:
- Hay dos lecturas paralelas: ledger por eventos y deuda por transacciones. La relación `event_id` debe permanecer obligatoria en la práctica.

## 14. Frontend Contract Gaps
Campos seguros hoy para frontend:
- `CreditCardRead.id`
- `name`
- `bank`
- `credit_limit`
- `cutoff_day`
- `due_day`
- `currency`
- `is_active`
- `estimated_current_debt`, con advertencia de que proviene de vista transaccional.
- `estimated_available_credit`, calculado como `credit_limit - estimated_current_debt`.
- `monthly_cc_payment`, solo como estimado actual, no como pago requerido formal.
- En status: `total_debt`, `billed_debt`, `unbilled_debt`, `available_credit`, `next_payment_due_date`.

Campos no seguros o inexistentes:
- `current_debt` de `credit_cards`: no consumir.
- `statement_balance`: no existe.
- `payment_required`: no existe en Credit; solo existe en Snapshot Truth con semántica ambigua.
- `next_payment_estimate`: no existe.
- `installments_paid`: no consumir como verdad; no se actualiza.
- `management_fee`, `monthly_interest_rate`, `annual_interest_rate`: existen en BD, no contrato API.

## 15. Risks
Riesgos identificados:
- Doble fuente `credit_cards.current_debt` vs deuda computada.
- `current_debt` obsoleto y divergente en producción.
- `payment_required` ambiguo y puede fallar por `next_payment_estimate` inexistente.
- `statement_balance` inexistente pese a estar en roadmap Sprint 5.
- Separación billed/unbilled recalculada sin statement persistido.
- Corte calculado con fecha UTC, no timezone de producto.
- Cuotas sin schedule real.
- `installments_paid` no actualizado.
- Tasas y cuota de manejo con datos productivos pero sin semántica API.
- Idempotencia puede dejar evento sin transacción si hubo fallo parcial histórico.
- Contratos frontend pueden inferir significados no garantizados.
- `monthly_cc_payment` puede confundirse con pago requerido.
- Pagos se aplican globalmente, no por ciclo ni statement.

## 16. Proposed V1.1 Semantics
Decisiones técnicas propuestas:

`source of truth de deuda`:
- Usar `credit_card_transactions` como fuente de verdad.
- Mantener `financial_events` como ledger/traceability obligatorio.
- No usar `credit_cards.current_debt` para cálculos.

`current_debt`:
- Definir API `current_debt = total_debt computado`.
- Deprecar el campo físico `credit_cards.current_debt`.

`available_credit`:
- `available_credit = max(0, credit_limit - current_debt)`.

`billed_debt`:
- Deuda de compras hasta el último corte, menos pagos aplicados primero a facturado.
- En V1.1 puede seguir computada, pero debe documentarse como aproximación sin statement persistido.

`unbilled_debt`:
- Deuda posterior al último corte, ajustada por pagos excedentes al facturado.

`statement_balance`:
- No declarar como verdad hasta tener statement/corte persistido.
- Si Sprint 5 necesita el campo, usar alias controlado de `billed_debt` llamado `statement_balance_estimate`, no `statement_balance` definitivo.

`payment_required`:
- V1.1 seguro: `payment_required = billed_debt`.
- Si `billed_debt == 0`, `payment_required = 0`.

`next_payment_estimate`:
- Exponer separado como `monthly_cc_payment` actual o nuevo campo `next_payment_estimate`.
- No mezclarlo con `payment_required` sin aprobación.

`installment schedule`:
- No existe hoy.
- Mantener estimación simple en V1.1 o crear schedule explícito solo con aprobación.

`management_fee`:
- Exponer como metadata de tarjeta.
- No generar cargos automáticos sin regla.

`monthly_interest_rate` y `annual_interest_rate`:
- Exponer como metadata informativa.
- No calcular intereses complejos.

## 17. Required Migrations
No ejecutar migraciones durante auditoría.

Migraciones potenciales para implementación, sujetas a aprobación:
- Crear o reemplazar vista semántica `v_credit_card_semantics` con `current_debt`, `available_credit`, `billed_debt`, `unbilled_debt`, `payment_required`, `next_payment_estimate`.
- Opcional: agregar comentario/metadata o documentación DB indicando que `credit_cards.current_debt` está deprecated.
- Opcional: agregar constraint o proceso de backfill para garantizar `credit_card_transactions.event_id NOT NULL` en datos nuevos.
- Opcional futuro: crear tabla `credit_card_installment_schedule` si se aprueba schedule real.
- No eliminar `current_debt` en V1.1 sin evaluar frontend/datos históricos.

## 18. Required Tests
Tests mínimos requeridos:
- `current_debt` API proviene de transacciones, no de `credit_cards.current_debt`.
- `available_credit = credit_limit - current_debt`.
- Compra aumenta deuda y no reduce balance de cuenta.
- Pago reduce balance de cuenta y deuda.
- Sobrepago se rechaza.
- `payment_required = billed_debt`.
- `payment_required = 0` cuando solo hay unbilled debt, si esa decisión se aprueba.
- Snapshot no intenta leer `next_payment_estimate` inexistente.
- `credit_card_purchase` no aparece como expense cash.
- `credit_card_payment` no aparece como expense de consumo.
- Cuotas múltiples calculan `monthly_amount`, pero no actualizan `installments_paid` sin schedule.
- Tasas/comisiones se exponen sin generar cargos automáticos.

## 19. Required Smokes
Smokes mínimos requeridos:
- Crear tarjeta.
- Crear compra 1 cuota.
- Crear compra múltiples cuotas.
- Validar deuda total y disponible.
- Validar billed/unbilled con compra antes/después de corte.
- Pagar parcialmente.
- Pagar total.
- Rechazar sobrepago.
- Validar snapshot con solo unbilled debt.
- Validar snapshot con billed debt.
- Validar cashflow: compra tarjeta neutral, pago tarjeta debt_service/outflow.
- Validar `/api/v1/credit/summary` y `/api/v1/intelligence/debt` consistentes.

## 20. Implementation Sequence
Secuencia mínima y segura:
1. Corregir contrato Python: agregar semánticas explícitas en schemas sin cambiar DB primero.
2. Eliminar uso de `next_payment_estimate` inexistente en snapshot o agregarlo explícitamente al status.
3. Centralizar cálculo en un único método de dominio Credit.
4. Hacer que Intelligence consuma solo ese método.
5. Marcar `credit_cards.current_debt` como obsoleto en documentación y evitar todo uso nuevo.
6. Exponer `management_fee` y tasas como metadata si se aprueba.
7. Agregar tests unitarios de semántica.
8. Agregar smoke Sprint 5.
9. Solo después evaluar migración de vista o schedule.

## 21. Questions Requiring Founder Approval
Preguntas que requieren decisión antes de implementar:
- ¿`payment_required` debe ser estrictamente `billed_debt` o puede usar estimación de cuotas futuras?
- ¿Se acepta que `statement_balance` en V1.1 sea `billed_debt` aproximado, o se requiere statement persistido real?
- ¿`next_payment_estimate` debe mostrarse al usuario aunque no sea exigible?
- ¿Se implementa schedule real de cuotas ahora o se mantiene estimación simple?
- ¿Management fee debe ser solo informativo o debe generar deuda mensual?
- ¿Intereses deben permanecer informativos hasta V1.2?
- ¿Se depreca formalmente `credit_cards.current_debt` sin eliminarlo físicamente?
- ¿Qué timezone debe regir cortes: `America/Bogota` en vez de UTC?
