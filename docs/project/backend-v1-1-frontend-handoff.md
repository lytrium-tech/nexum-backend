# Backend V1.1 Frontend Handoff

## 1. Executive Summary
Backend V1.1 resolves the critical product, financial, and conversational inconsistencies identified during integration. It enforces a strict boundary of responsibility:
* **Backend calculates**: Handles all financial math, limits, and statuses deterministically.
* **Frontend represents**: Renders data and state returned by the API; performs no financial logic.
* **LLM explains**: Contextualizes user data without calculating metrics.

This document serves as the single source of truth for frontend developers to transition their applications from Backend V1 to V1.1.

## 2. Backend V1 → V1.1 Overview
* **Financial Truth**: Unified cash and liability calculations via a single snapshot.
* **Categories**: Safe lifecycle with reactivation support and fallback logic for unclassified items.
* **Goals**: Dynamic period calculations with native support for flexible savings.
* **Obligations**: Supports multiple modes (fixed, partial, variable) and tracks remaining period totals.
* **Credit Semantics**: Calculated dynamically from transactions instead of static database columns.
* **Transfers**: Handled as balance adjustments without skewing net income or expenses.
* **Conversations**: Robust intent handling with explicit state management (clarifications, confirmations).

## 3. Source Of Truth
The backend API is the **only** source of truth for any financial calculation or user status. The frontend must consume pre-computed endpoints and must **never** compute metrics, balances, or statuses locally.

## 4. Financial Truth
El estado financiero oficial del usuario se centraliza en el nodo `truth` de `/api/v1/intelligence/snapshot`.
* **available_real**: Sumatoria del dinero disponible real en cuentas activas líquidas.
* **committed_outflows**: Egresos comprometidos del periodo actual. Suma de obligaciones pendientes del periodo, deuda facturada de tarjeta de crédito (`payment_required`) y el ahorro mensual de metas requeridas en el periodo (`goals_required_this_period`).
* **free_money**: La verdad financiera oficial ("dinero libre" real). Calculado como:
  $$\text{free\_money} = \max(0, \text{available\_real} - \text{committed\_outflows})$$

El frontend debe mostrar este valor (`free_money`) como el saldo oficial disponible para gastar en el Home/Dashboard del usuario.

## 5. Snapshot V1.1
El endpoint `GET /api/v1/intelligence/snapshot` retorna el esquema `IntelligenceSnapshotRead` que incluye el nodo `truth` (`SnapshotTruth`) con los siguientes campos:
* `available_real` (string/decimal): Saldo real consolidado.
* `committed_outflows` (string/decimal): Suma de egresos comprometidos.
* `free_money` (string/decimal): Saldo neto libre para gastar.
* `safe_money` (string/decimal): Dinero seguro (actualmente equivalente a `free_money`).
* `payment_required` (string/decimal): Suma de pagos mínimos / facturados exigibles de inmediato.
* `goals_required_this_period` (string/decimal): Requerimiento de ahorro mensual sumado de todas las metas activas.
* `calculation_warnings` (array): Mensajes de advertencia si existen discrepancias.
* `data_quality` (object): Diccionario con metadata de calidad de datos.

## 6. Goals V1.1
Las metas se calculan dinámicamente en backend y se exponen en `GoalRead`:
* **monthly_required**: Requerimiento mensual calculado como $\text{remaining\_amount} / \text{months\_left}$.
* **remaining_required_this_period**: Dinero faltante por aportar en el periodo mensual corriente.
* **is_flexible**: `true` si la meta no especifica `target_date`.
  * Las metas flexibles devuelven `required_this_period = 0.00` y `period_status = "flexible"`.
  * **No** suman al flujo de liquidez comprometido en `committed_outflows`.
* **Aportes extras**: No hay bloqueos por sobre-cubrir o aportar de más. Si se aporta de más en un mes, el cálculo mensual restante se ajusta automáticamente a la baja en los siguientes periodos y el estado se marca como `covered` o `completed`.

## 7. Obligations V1.1
El esquema `ObligationRead` expone la realidad de obligaciones activas:
* **payment_mode**: Modos admitidos (`fixed_full_payment`, `partial_allowed`, `variable_amount`).
* **remaining_amount**: Monto pendiente por pagar en el periodo actual.
* **period_status**: Estado de pago (`pending`, `partial`, `covered`, `overdue`, `paid`).
* **is_pending**: Booleano que indica si la obligación requiere atención/pago en el periodo actual.
* **Reglas de Pago**:
  * `fixed_full_payment`: Exige pago exacto. Rechaza montos mayores, menores o un segundo pago si ya está cubierto (error 409/422).
  * `partial_allowed`: Permite múltiples abonos acumulativos de cualquier monto reduciendo `remaining_amount`.
  * `variable_amount`: Permite pagos de montos libres sin restricciones.

## 8. Credit Semantics V1.1
El saldo de tarjetas se calcula desde transacciones (`credit_card_transactions` asociadas a `financial_events`) y se expone en `CreditCardRead`:
* **current_debt**: Deuda total vigente real.
* **total_debt**: Alias de `current_debt` para visualización frontend.
* **billed_debt**: Deuda ya facturada que el usuario debe pagar.
* **unbilled_debt**: Compras realizadas pendientes de facturación.
* **available_credit**: Línea de crédito disponible ($\text{credit\_limit} - \text{current\_debt}$).
* **payment_required**: Se establece que:
  $$\text{payment\_required} = \text{billed\_debt}$$
* **next_payment_estimate**: Estimado del siguiente pago basado en distribución simple de cuotas.
* **statement_balance**: Extracto oficial. Importante:
  $$\text{statement\_balance puede venir null}$$
* **Metadata**: `franchise`, `network`, `management_fee`, etc., no disparan cobros automáticos en backend (son informativas).

## 9. Transfers V1.1
Movimientos entre cuentas propias (`GET`/`POST` `/api/v1/transfers`):
* **Impacto**: Debita la cuenta origen y acredita la cuenta destino.
* **Efecto Cashflow**:
  * `transfer_in` y `transfer_out` **no** afectan `income` ni `expenses`.
  * **No** afectan `net_cashflow` global para evitar duplicaciones o distorsión de ingresos/egresos netos del mes.

## 10. Conversations V1.1
Flujo conversacional en `POST /api/v1/conversations/message`:
* **Entrada (`ConversationalRequest`)**: `message`, `channel`, `external_message_id`, `pending_action_id`.
* **Salida (`ConversationalResponse`)**: `response_text`, `intent`, `status` (`completed`, `awaiting_clarification`, `awaiting_confirmation`, `cancelled`, `error`), `trace_id`, `pending_action_id`, `structured_data`.
* **Confirmación**: Si el estado es `awaiting_confirmation`, el backend retorna un `pending_action_id`. El frontend debe mostrar un modal de confirmación (ej. Botones Sí/No) y reenviar el `pending_action_id` junto a la respuesta del usuario en la siguiente petición.

## 11. Ownership Rules
Aislamiento multi-inquilino estricto en base al token de Supabase (`Authorization: Bearer <TOKEN>`). Peticiones que intenten leer o modificar entidades de otros usuarios resultarán en errores `404 Not Found` o `403 Forbidden`. El frontend no debe enviar parámetros de pertenencia.

## 12. Traceability
Todos los eventos financieros generados por el backend (transacciones, pagos, aportes) contienen el `trace_id` de la petición o comando origen que los creó. Esto permite al frontend rastrear e inspeccionar la procedencia de cualquier movimiento desde la interfaz de usuario.

## 13. Contracts Added
Nuevas firmas de API incluidas en `openapi.json`:
* **Esquemas**: `SnapshotTruth`, `CreditCardInstallment`, `TransferCreate`, `TransferResult`.
* **Nuevos Campos**:
  * `GoalRead`: `monthly_required`, `required_this_period`, `contributed_this_period`, `remaining_required_this_period`, `period_status`, `is_flexible`.
  * `ObligationRead`: `remaining_amount`, `period_status`, `is_pending`, `payment_mode`.
  * `CreditCardRead`: `current_debt`, `total_debt`, `billed_debt`, `unbilled_debt`, `payment_required`, `next_payment_estimate`, `statement_balance`, `available_credit`, `network`, `franchise`.

## 14. Legacy Aliases Still Available
Se mantienen los siguientes campos deprecados en `CreditCardRead` para evitar romper el frontend actual durante la transición:
* `estimated_current_debt` (alias de `current_debt`)
* `monthly_cc_payment` (alias of `next_payment_estimate`)
* `estimated_available_credit` (alias de `available_credit`)

## 15. Deprecated Concepts
* **Calcular deuda de tarjeta localmente**: Sumar transacciones o fiarse de variables estáticas de cuenta en cliente.
* **Calcular dinero libre localmente**: Derivar saldos netos restando pasivos de forma manual en UI.
* **Clasificar transferencias como gastos/ingresos**: Sumar volumen de transferencias a reportes de flujo de caja.

## 16. What Frontend Must Stop Calculating
```text
Frontend must stop calculating:
```
1. **Dinero Libre (`free_money`)**: Leer directamente de `snapshot.truth.free_money`.
2. **Dinero Comprometido (`committed_outflows`)**: Leer directamente de `snapshot.truth.committed_outflows`.
3. **Estado de Obligaciones (`is_pending` / `remaining_amount`)**: Usar el campo `is_pending` y `remaining_amount` de la API de obligaciones.
4. **Cálculos Mensuales de Metas (`monthly_required` / `remaining_required_this_period`)**: Consumir directamente los campos de `GoalRead`.
5. **Métricas de Tarjeta de Crédito (`current_debt` / `available_credit`)**: Confiar en la respuesta precalculada de `CreditCardRead`.
6. **Cashflow Neto**: Excluir transferencias de los cálculos mensuales de ingresos y gastos de consumo.

## 17. Frontend Update Checklist
* [ ] Actualizar modelos y clientes API consumiendo el nuevo contrato `openapi.json`.
* [ ] Reemplazar cálculos locales de Home Dashboard por los valores expuestos en `snapshot.truth` (`free_money`, `committed_outflows`, etc.).
* [ ] Actualizar tarjetas de crédito para usar `current_debt`, `billed_debt`, `unbilled_debt` y `payment_required`.
* [ ] Validar flujos de pago a tarjetas de crédito del lado cliente para evitar sobrepagos mayores a `current_debt`.
* [ ] Adaptar interfaz de Metas para ocultar requerimientos en metas con `is_flexible = true` y mostrar `remaining_required_this_period`.
* [ ] Adaptar interfaz de Obligaciones para usar la bandera `is_pending` y aplicar validaciones visuales según `payment_mode`.
* [ ] Implementar la pantalla/modal de confirmación conversacional cuando `/conversations/message` devuelva `awaiting_confirmation` con un `pending_action_id`.
* [ ] Asegurar que las transferencias no incrementen los totales de ingresos ni gastos en gráficos de flujo de caja.

## 18. Known Limitations
* **Statement Balance**: `statement_balance` se expone como `null` ya que no persistimos extractos bancarios cerrados en esta versión.
* **Sin Motor de Intereses**: Los intereses de tarjeta de crédito, cobros de mora y cuotas de manejo no se calculan de manera automática; deben registrarse mediante transacciones manuales.
* **Sin Recálculo de Metas Flexibles**: El ajuste recomendado para metas flexibles no se ejecuta automáticamente en V1.1 (mapeado para V1.2+).
* **Sin Endpoint de Historial de Chats**: Las conversaciones se realizan mensaje por mensaje.

## 19. Alpha Ready Status
El Backend V1.1 se encuentra en estado **Alpha Ready** cerrado y validado. Cuenta con 142 pruebas unitarias superadas, regresión local completa de smokes y despliegue productivo validado en VPS. Los endpoints públicos `/health` y `/health/readiness` responden con estado saludable (`healthy`).
