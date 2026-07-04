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

## 3. Endpoints V1.7 Propuestos (Obligaciones, Periodos y Pagos)

### 3.1. Obligaciones
- `GET /api/v1.7/obligations`
  - *Retorna la lista de obligaciones del usuario (sin incluir sus periodos detallados, solo estado general).*
- `POST /api/v1.7/obligations`
  - *Crea un nuevo template de obligación y genera los periodos iniciales correspondientes.*
- `GET /api/v1.7/obligations/{obligation_id}`
  - *Retorna detalles de la obligación, incluyendo la lista paginada/actual de sus periodos.*
- `PATCH /api/v1.7/obligations/{obligation_id}`
  - *Actualiza metadatos de la obligación (ej. nombre).*
- `POST /api/v1.7/obligations/{obligation_id}/archive`
  - *Archiva la obligación para que no genere más periodos, manteniéndola en el historial.*
- `POST /api/v1.7/obligations/{obligation_id}/cancel`
  - *Cancela la obligación.*

### 3.2. Periodos
- `GET /api/v1.7/obligations/{obligation_id}/periods`
  - *Consulta los periodos individuales de una obligación.*
- `POST /api/v1.7/obligation-periods/{period_id}/define-amount`
  - *Define el monto a pagar para periodos de monto variable (`amount_type=variable`) que estén en `pending_amount_definition`.*
- `POST /api/v1.7/obligation-periods/{period_id}/pay`
  - *Aplica un pago parcial o total a un periodo específico.*
- `POST /api/v1.7/obligation-periods/{period_id}/skip`
  - *Marca un periodo impago como saltado (no genera deuda y no requiere pago).*
- `POST /api/v1.7/obligation-periods/{period_id}/cancel`
  - *Cancela un periodo específico.*

### 3.3. Pagos Generales
- `POST /api/v1.7/obligations/pay`
  - *Procesa un pago global con estrategia FIFO (First-In-First-Out). Aplica el monto a las obligaciones/periodos más antiguos vencidos o pendientes.*

## 4. FX Endpoints Propuestos (Transversales)

- `GET /api/v1/fx/rates/latest`
  - *Obtiene las tasas cacheadas del backend. Devuelve `fetched_at`, `expires_at`, proveedor y un flag `is_stale` si la tasa no ha podido ser actualizada.*
- `POST /api/v1/fx/quote`
  - *Recibe un `amount`, `from_currency`, `to_currency` y devuelve la conversión calculada por el backend junto a un `quote_id`, `rate` y `expires_at`. Esto puede usarse como pre-confirmación antes del pago final.*

## 5. Schemas Conceptuales

### 5.1. Obligation Schemas
```json
// POST /api/v1.7/obligations
// ObligationV17Create
{
  "name": "string",
  "obligation_type": "recurring | one_time",
  "frequency": "monthly | weekly | biweekly | yearly | one_time",
  "amount_type": "fixed | variable",
  "base_amount": "float | null",
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
  "base_amount": "float | null",
  "currency": "string",
  "status": "active | completed | archived | cancelled",
  "current_period_id": "uuid | null",
  "created_at": "datetime"
}
```

### 5.2. Period & Payment Schemas
```json
// ObligationPeriodV17Response
{
  "id": "uuid",
  "obligation_id": "uuid",
  "sequence_number": "int",
  "amount_due": "float | null",
  "amount_paid": "float",
  "currency": "string",
  "due_date": "date",
  "status": "pending_amount_definition | pending_payment | partially_paid | paid | overdue | skipped | cancelled"
}

// POST /api/v1.7/obligation-periods/{period_id}/define-amount
// DefinePeriodAmountRequest
{
  "amount_due": "float"
}

// POST /api/v1.7/obligation-periods/{period_id}/pay
// PayObligationPeriodRequest
{
  "amount": "float",
  "source_account_id": "uuid",
  "quote_id": "uuid | null"
}

// POST /api/v1.7/obligations/pay (FIFO)
// PayObligationsFIFORequest
{
  "amount": "float",
  "source_account_id": "uuid",
  "currency": "string",
  "quote_id": "uuid | null"
}

// ObligationPaymentV17Response
{
  "id": "uuid",
  "period_id": "uuid | null",
  "amount_applied": "float",
  "currency": "string",
  "source_amount": "float",
  "source_currency": "string",
  "fx_rate": "float | null"
}
```

### 5.3. FX Schemas
```json
// GET /api/v1/fx/rates/latest
// FXRatesLatestResponse
{
  "base_currency": "USD",
  "rates": {
    "COP": 4150.50,
    "EUR": 0.92
  },
  "fetched_at": "datetime",
  "expires_at": "datetime",
  "provider": "string",
  "is_stale": "boolean"
}

// POST /api/v1/fx/quote
// FXQuoteRequest
{
  "amount": "float",
  "from_currency": "string",
  "to_currency": "string"
}

// FXQuoteResponse
{
  "quote_id": "uuid",
  "source_amount": "float",
  "target_amount": "float",
  "from_currency": "string",
  "to_currency": "string",
  "rate": "float",
  "expires_at": "datetime"
}
```

### 5.4. Common Schemas
```json
// ApiErrorResponse
{
  "error_code": "string",
  "message": "string",
  "details": "dict"
}

// EmptyStateResponse
{
  "data": [],
  "metadata": {
    "empty_reason": "string"
  }
}
```

## 6. Enumeraciones

- **`obligation_type`**: `recurring`, `one_time`
- **`frequency`**: `monthly`, `weekly`, `biweekly`, `yearly`, `one_time`
- **`amount_type`**: `fixed`, `variable`
- **`period_status`**: `pending_amount_definition`, `pending_payment`, `partially_paid`, `paid`, `overdue`, `skipped`, `cancelled`
- **`obligation_status`**: `active`, `completed`, `archived`, `cancelled`
- **`payment_application_strategy`**: `specific_period`, `fifo`
- **`fx_quote_status`**: `active`, `expired`, `used`, `rejected`

## 7. Error Codes (Controlados)
- `invalid_obligation_type`: Configuración de obligación incompatible con el tipo.
- `invalid_frequency`: Frecuencia no soportada para el tipo de obligación.
- `invalid_amount`: Monto negativo o no representativo.
- `invalid_currency`: Moneda no soportada por el sistema.
- `period_not_found`: Periodo inexistente.
- `obligation_not_found`: Obligación inexistente.
- `period_already_paid`: Intento de pago sobre un periodo en estado `paid`.
- `period_cancelled`: Operación inválida sobre periodo cancelado.
- `period_skipped`: Operación inválida sobre periodo saltado.
- `amount_definition_required`: Intento de pago de un periodo variable sin monto definido.
- `amount_already_defined`: Intento de redefinir un monto ya establecido.
- `overpayment_not_allowed`: El monto excede la deuda actual del periodo/obligación.
- `fx_rate_unavailable`: Proveedor de FX caído y sin cache válido.
- `fx_quote_expired`: La cotización referenciada caducó.
- `fx_quote_changed`: El spread o la tasa base varió drásticamente durante la operación.
- `feature_flag_disabled`: El usuario/sistema intentó acceder a V1.7 sin estar habilitado.
- `legacy_endpoint_untouched`: Validación que confirma que un error no es atribuible a V1.5.

## 8. Empty States
- **Usuario sin obligaciones:** Lista vacía, `empty_reason: "no_obligations"`.
- **Obligación sin periodos generados:** Lista vacía, `empty_reason: "no_periods_generated"`.
- **Periodo variable pendiente de monto:** `amount_due: null`, el dashboard asume $0 para no generar deuda fantasma.
- **No hay tasas FX disponibles:** `is_stale: true`, con rates en null o valores legacy muy viejos.
- **Tasa FX stale:** El frontend presenta un aviso amarillo advirtiendo volatilidad.

## 9. Backend Truth Rules (Reglas Financieras)
- **Backend calcula dinero:** Toda lógica matemática de consolidación o conversión de divisas reside y se resuelve en el backend.
- **Frontend previsualiza:** El frontend aplica estimaciones locales usando la tasa cacheada *solo para UI fluida*.
- **LLM no calcula dinero:** Agentes conversacionales no resuelven FX ni deudas; confían en el backend.
- **Frontend nunca envía conversión como verdad:** El payload de pago siempre es el monto origen (`source_amount`); el backend lo convierte.
- **Backend guarda monto, moneda, rate/source:** Los eventos financieros persisten inmutablemente `source_amount`, `source_currency`, `fx_rate` y `rate_timestamp`.

## 10. Compatibility with V1.5
- **Endpoints V1.5 no se tocan:** Todo router y schema bajo `/api/v1/obligations` permanece idéntico.
- **DB V1.5 debe seguir aceptando writes:** Se mantienen relajadas las constraints `NOT NULL` huérfanas de V1.6 hasta el despliegue pleno y migración de datos de V1.7.
- **V1.7 debe poder apagarse por feature flag:** Aunque las rutas sean `v1.7`, el acceso transaccional o la UI dependerán de un Feature Flag global o por usuario.
- **Dashboard/Intelligence deben seguir funcionando si V1.7 está off:** Las consultas legacy resuelven desde los esquemas bases sin afectar a usuarios V1.5.

## 11. OpenAPI Strategy
- **Generación y actualización:** Los esquemas se declararán usando Pydantic V2 en `app/obligations/schemas_v17.py`, lo que poblará automáticamente el `openapi.json` interactivo.
- **Frontend tipado:** El cliente generará sus interfaces TypeScript a partir del `openapi.json` versionado, previniendo discrepancias de contrato.
- **Tests de contrato obligatorios:** Pruebas que validan la conformidad del backend con el OpenAPI Spec y garantizan que los esquemas V1.5 continúan presentes y válidos.
- **Ningún cambio rompe V1.5:** El OpenAPI Spec contendrá ambas versiones en paralelo (`/api/v1/...` y `/api/v1.7/...`).

## 12. Contract Test Plan (Para Fases 1/3)
Las siguientes validaciones automatizadas deben ser implementadas:
1. Validar que los esquemas Pydantic V1.7 aceptan payloads válidos.
2. Validar que los esquemas rechazan parámetros faltantes o nulos donde son requeridos.
3. Asegurar mapeos correctos para los Error Codes definidos.
4. Validar que `openapi.json` contiene la nueva jerarquía de rutas `/api/v1.7/obligations`.
5. Validar explícitamente que `openapi.json` sigue conteniendo la jerarquía `/api/v1/obligations` intacta.
6. FX quote response estable: Simular caídas de proveedor y confirmar respuestas cacheadas `is_stale=true`.
7. Los ejemplos generados por FastAPI/Pydantic (`payload examples`) deben coincidir estructuralmente con las respuestas reales probadas.
