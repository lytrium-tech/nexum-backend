> **HISTORICAL NOTICE:** Este documento conserva diseños, planes o reportes históricos. No debe ser considerado la fuente de verdad actual. Para la documentación técnica canónica, consulte [docs/project/](../project/00-backend-overview.md).

# Nexum Core Obligations V1.7 — Phase 1 Contract Design & FX

## 1. Objetivo del contrato
Este documento define los contratos (endpoints, payloads, responses y error codes) para resolver los problemas de Core Obligations V1.7. Se consolidan las siguientes premisas conceptuales:
- **Obligation**: Es el template o regla base.
- **ObligationPeriod**: Es la instancia pagable de un ciclo específico.
- **ObligationPayment**: Es el pago aplicado a un periodo/obligación.
- **FX (Foreign Exchange)**: Es un servicio transversal que provee una previsualización instantánea de conversión en frontend, garantizando que el cálculo definitivo siempre ocurre y se audita en el backend.

## 2. Versioning Strategy
**Recomendación:** Rutas explícitas `/api/v1.7/obligations`.
*Justificación:* Separar los endpoints en una nueva jerarquía de rutas aísla completamente el contrato V1.7 de V1.5. Esto permite que el backend soporte simultáneamente a clientes usando la versión anterior sin requerir lógicas complejas de ruteo interno basadas en headers. Minimiza el riesgo de que una modificación en el Pydantic Schema de V1.7 rompa silenciosamente la deserialización de un payload de V1.5.

Para el caso de FX, la ruta recomendada es `/api/v1/fx/...`, ya que el motor de divisas es transversal al sistema (Metas, Ledger, Dashboard, Obligaciones) y no es exclusivo de V1.7.

## 3. Money Precision Rule
**Regla explícita:** Todo campo monetario debe implementarse obligatoriamente como tipo `Decimal` en el backend (Pydantic / Python) y como `NUMERIC` o `DECIMAL` en PostgreSQL. 
*No se debe usar el tipo `float` para representar dinero en la implementación real de Nexum, previniendo errores de precisión de punto flotante.*

## 4. Payment Amount Semantics
Para operaciones de pago (particularmente en escenarios multimoneda), el contrato establece una semántica estricta:
- El Frontend **siempre envía `source_amount` y `source_currency`** (es decir, el dinero que sale de la cuenta bancaria del usuario).
- El Backend **calcula el `target_amount`** contra la moneda del periodo/obligación.

*Ejemplo:*
Obligación: `10000 COP`
Cuenta origen: `USD`
- Frontend envía: `source_amount: 2.54`, `source_currency: USD`, `source_account_id: <uuid>`
- Backend calcula/aplica: Deduce los 2.54 USD de la cuenta, calcula la tasa (ej. 1 USD = 4150 COP), y aplica el `target_amount` (ej. 10541 COP, cubriendo la obligación y el sobrepago/rechazo según reglas), persistiendo la tasa (`fx_rate`).

## 5. Idempotency for Payments
Dado que los pagos desencadenan deducciones en el Ledger, deben estar protegidos contra reintentos por latencia de red.
- Los requests que ejecutan pagos deben incluir un `idempotency_key` generado por el frontend (ej. UUID).
- **Reglas:** Los pagos son estrictamente idempotentes. Un retry del frontend no debe duplicar pagos ni deducir saldo doblemente. El backend debe detectar un `idempotency_key` repetido (agrupado por usuario/operación) y devolver exactamente el mismo resultado lógico/JSON que la petición original exitosa.

## 6. FX Quote Expiration and Tolerance
El sistema de cotizaciones (quotes) balancea UX rápida vs riesgo cambiario:
- **`quote expires_at`**: Toda cotización entregada al frontend tiene una vigencia (ej. 5 minutos).
- **`quote_tolerance_bps`**: El backend permite un margen porcentual o en puntos básicos (basis points) de fluctuación de la tasa base si se ejecuta un pago sin un `quote_id` firme.
- **Regla de Ejecución:**
  - Si *no hay* `quote_id`, el backend calcula la conversión con la tasa vigente en ese instante.
  - Si *hay* `quote_id` vigente, el backend usa y honra la tasa de esa quote.
  - Si la quote expiró, el backend aborta la transacción con `fx_quote_expired`. El frontend debe actualizar la tasa y pedir confirmación.
  - Si la tasa de mercado varió drásticamente y supera la tolerancia máxima, el backend aborta y retorna `fx_quote_changed`.

## 7. Endpoints V1.7 Propuestos (Obligaciones, Periodos y Pagos)

### 7.1. Obligaciones
- `GET /api/v1.7/obligations` (Lista general).
- `POST /api/v1.7/obligations` (Creación e instanciación).
- `GET /api/v1.7/obligations/{obligation_id}` (Detalle).
- `PATCH /api/v1.7/obligations/{obligation_id}` (Actualización).
- `POST /api/v1.7/obligations/{obligation_id}/archive` (Archivar).
- `POST /api/v1.7/obligations/{obligation_id}/cancel` (Cancelar).

### 7.2. Periodos
- `GET /api/v1.7/obligations/{obligation_id}/periods`
- `POST /api/v1.7/obligation-periods/{period_id}/define-amount`
- `POST /api/v1.7/obligation-periods/{period_id}/pay`
- `POST /api/v1.7/obligation-periods/{period_id}/skip`
- `POST /api/v1.7/obligation-periods/{period_id}/cancel`

### 7.3. Pagos Generales
- `POST /api/v1.7/obligations/pay` (FIFO: Paga las obligaciones/periodos más antiguos vencidos o pendientes).

## 8. FX Endpoints Propuestos (Transversales)
- `GET /api/v1/fx/rates/latest` (Obtiene tasas cacheadas, timestamps y vigencia).
- `POST /api/v1/fx/quote` (Genera una cotización específica `quote_id` con vigencia y monto exacto).

## 9. Schemas Conceptuales (JSON & Pydantic)

### 9.1. Obligation Schemas
```json
// POST /api/v1.7/obligations
// ObligationV17Create
{
  "name": "string",
  "obligation_type": "recurring | one_time",
  "frequency": "monthly | weekly | biweekly | yearly | one_time",
  "amount_type": "fixed | variable",
  "base_amount": "Decimal | null",
  "currency": "string",
  "start_date": "date | null",
  "end_date": "date | null",
  "end_count": "int | null",
  "due_day": "int | null"
}

// ObligationV17Response
{
  "id": "uuid",
  "name": "string",
  "obligation_type": "string",
  "frequency": "string",
  "amount_type": "string",
  "base_amount": "Decimal | null",
  "currency": "string",
  "status": "active | completed | archived | cancelled",
  "current_period_id": "uuid | null",
  "created_at": "datetime"
}
```

### 9.2. Period & Payment Schemas
```json
// ObligationPeriodV17Response
{
  "id": "uuid",
  "obligation_id": "uuid",
  "sequence_number": "int",
  "amount_due": "Decimal | null",
  "amount_paid": "Decimal",
  "currency": "string",
  "due_date": "date",
  "status": "pending_amount_definition | pending_payment | partially_paid | paid | overdue | skipped | cancelled"
}

// POST /api/v1.7/obligation-periods/{period_id}/define-amount
// DefinePeriodAmountRequest
{
  "amount_due": "Decimal"
}

// POST /api/v1.7/obligation-periods/{period_id}/pay
// PayObligationPeriodRequest
{
  "source_amount": "Decimal",
  "source_account_id": "uuid",
  "quote_id": "uuid | null",
  "idempotency_key": "string"
}

// POST /api/v1.7/obligations/pay (FIFO)
// PayObligationsFIFORequest
{
  "source_amount": "Decimal",
  "source_account_id": "uuid",
  "quote_id": "uuid | null",
  "idempotency_key": "string"
}

// ObligationPaymentV17Response
{
  "id": "uuid",
  "period_id": "uuid | null",
  "amount_applied": "Decimal",
  "currency": "string",
  "source_amount": "Decimal",
  "source_currency": "string",
  "fx_rate": "Decimal | null"
}
```

### 9.3. FX Schemas
```json
// GET /api/v1/fx/rates/latest
// FXRatesLatestResponse
{
  "base_currency": "USD",
  "rates": {
    "COP": "4150.50",
    "EUR": "0.92"
  },
  "fetched_at": "datetime",
  "expires_at": "datetime",
  "provider": "string",
  "is_stale": "boolean"
}

// POST /api/v1/fx/quote
// FXQuoteRequest
{
  "amount": "Decimal",
  "from_currency": "string",
  "to_currency": "string",
  "idempotency_key": "string | null"
}

// FXQuoteResponse
{
  "quote_id": "uuid",
  "source_amount": "Decimal",
  "target_amount": "Decimal",
  "from_currency": "string",
  "to_currency": "string",
  "rate": "Decimal",
  "expires_at": "datetime"
}
```

## 10. Financial Event Audit Fields
Para garantizar la trazabilidad de todos los pagos y sus efectos en el Ledger V1.7, la tabla/entidad `financial_events` (o `obligation_payments` extendida) debe ser capaz de persistir inmutablemente los siguientes datos de auditoría:
- `source_amount`: Monto deducido de la cuenta.
- `source_currency`: Moneda de la cuenta.
- `target_amount`: Monto aplicado al pasivo (Obligación).
- `target_currency`: Moneda del pasivo.
- `fx_rate`: Tasa de conversión efectiva utilizada.
- `fx_provider`: Origen de la tasa (ej. DolarAPI).
- `fx_rate_timestamp`: Marca de tiempo en la que el proveedor originó esa tasa.
- `quote_id`: Identificador de la cotización bloqueada (si aplica).
- `idempotency_key`: Llave de deduplicación del request originador.

## 11. Enumeraciones y Códigos de Error

### 11.1. Enums
- **`obligation_type`**: `recurring`, `one_time`
- **`frequency`**: `monthly`, `weekly`, `biweekly`, `yearly`, `one_time`
- **`amount_type`**: `fixed`, `variable`
- **`period_status`**: `pending_amount_definition`, `pending_payment`, `partially_paid`, `paid`, `overdue`, `skipped`, `cancelled`
- **`obligation_status`**: `active`, `completed`, `archived`, `cancelled`
- **`payment_application_strategy`**: `specific_period`, `fifo`
- **`fx_quote_status`**: `active`, `expired`, `used`, `rejected`

### 11.2. Error Codes
- `invalid_obligation_type`: Configuración incompatible con el tipo.
- `invalid_frequency`: Frecuencia no soportada.
- `invalid_amount`: Monto negativo o nulo.
- `invalid_currency`: Moneda no soportada.
- `period_not_found` / `obligation_not_found`: Entidad inexistente.
- `period_already_paid` / `period_cancelled` / `period_skipped`: Estado inválido para pago.
- `amount_definition_required`: Pago sobre periodo variable sin monto definido.
- `amount_already_defined`: Intento de redefinir monto.
- `overpayment_not_allowed`: Exceso de pago sobre la deuda actual.
- `fx_rate_unavailable`: Proveedor caído.
- `fx_quote_expired`: Cotización caducada.
- `fx_quote_changed`: Variación de mercado supera tolerancia.

## 12. OpenAPI Examples

### 12.1. Pago COP desde cuenta COP (Sin FX)
**Request:**
```json
{
  "source_amount": "10000.00",
  "source_account_id": "acc-uuid-1234",
  "quote_id": null,
  "idempotency_key": "req-uuid-abc-1"
}
```
**Response (200 OK):**
```json
{
  "id": "pay-uuid-5678",
  "period_id": "per-uuid-4321",
  "amount_applied": "10000.00",
  "currency": "COP",
  "source_amount": "10000.00",
  "source_currency": "COP",
  "fx_rate": null
}
```

### 12.2. Pago COP desde cuenta USD (Con FX Quote)
**Request:**
```json
{
  "source_amount": "2.50",
  "source_account_id": "usd-acc-uuid-9999",
  "quote_id": "quote-uuid-8888",
  "idempotency_key": "req-uuid-abc-2"
}
```
**Response (200 OK):**
```json
{
  "id": "pay-uuid-7777",
  "period_id": "per-uuid-4321",
  "amount_applied": "10375.00",
  "currency": "COP",
  "source_amount": "2.50",
  "source_currency": "USD",
  "fx_rate": "4150.00"
}
```

### 12.3. Retry de pago (Idempotency Key Repetido)
**Request:** Mismo request del punto 12.1 (con `idempotency_key: "req-uuid-abc-1"`).
**Response (200 OK):** Exactamente el mismo JSON del punto 12.1, sin deducciones extra en la base de datos.

### 12.4. Cotización Expirada
**Request:** Pago con `quote_id: "quote-expired-1111"`.
**Response (422 Unprocessable Entity):**
```json
{
  "error_code": "fx_quote_expired",
  "message": "The provided FX quote has expired. Please refresh the exchange rate and try again.",
  "details": {}
}
```

### 12.5. Variable Period Pending Amount Definition
**Request:**
```json
{
  "amount_due": "55000.00"
}
```
**Response (200 OK):**
```json
{
  "id": "per-uuid-9090",
  "obligation_id": "obl-uuid-4444",
  "sequence_number": 3,
  "amount_due": "55000.00",
  "amount_paid": "0.00",
  "currency": "COP",
  "due_date": "2026-07-15",
  "status": "pending_payment"
}
```

## 13. Empty States & V1.5 Compatibility
- **Empty States:** Listas devuelven `[]` con metadata detallando `empty_reason` (ej. `"no_obligations"`). Periodos pendientes de monto reportan deuda nula o cero en el dashboard para evitar deuda fantasma.
- **Backend Truth:** Backend calcula el dinero final siempre. El cliente es una capa pasiva de visualización.
- **Compatibility:** Todo el contrato en `/api/v1.7` es aditivo. No modifica `/api/v1` ni corrompe respuestas legacy del frontend V1.5. Las bases de datos se adaptarán mediante anulaciones `NOT NULL` transitorias para garantizar interoperabilidad dual.

