# Nexum Backend V1.6 — Obligations Core Sprint 0 Architecture

## 1. Executive Summary
El objetivo de Backend V1.6 es reconstruir el core de Obligaciones (Obligations) separando conceptualmente la plantilla o regla general (`Obligation`) de sus ocurrencias en el tiempo (`ObligationPeriod`). Esta arquitectura habilita pagos futuros reales, montos variables seguros, soporte estricto de multimoneda, asignación FIFO de deudas, y simplifica la integración futura con interfaces basadas en IA (WhatsApp).

## 2. Current State Audit
Tras auditar `app/obligations/`, se identificó:
- **Modelos:** Solo existen `Obligation` y `ObligationPayment`. No existe la entidad `ObligationPeriod`.
- **Lógica de estado (Deuda técnica):** Los estados (`overdue`, `pending`, `partial`) y balances (`remaining_amount`) se calculan "on-the-fly" dentro del modelo Pydantic (`ObligationRead`) basándose en `metadata["skip_periods"]`, `paid_this_period` y `datetime.now()`.
- **Consultas DB:** Es extremadamente ineficiente (y casi imposible) consultar desde SQL qué obligaciones están vencidas o cuánto es la deuda total.
- **Riesgos:** La reescritura requerirá eliminar gran parte de `app/obligations/schemas.py`, modificar `service.py`, y reescribir por completo la suite de pruebas `tests/unit/test_obligations_semantics.py`.
- **OpenAPI:** Los contratos actuales se romperán, requiriendo actualización para los clientes (frontend).

## 3. Product Rules Confirmed
1. **Tipos:** Recurrente indefinida, recurrente fin por fecha, recurrente por cuotas, y one-time.
2. **Montos:** Fijos (se copian al periodo) y Variables (periodo nace sin monto, debe definirse).
3. **Abonos:** Siempre permitidos, permitiendo `partially_paid` hasta cubrir el monto total.
4. **Periodos:** Basados en calendario natural (ej. mes).
5. **Pagos Futuros:** Permitidos como pagos reales, no reservas.
6. **Regla FIFO:** Pagos generales aplican: Vencidos -> Actual -> Futuros.
7. **Frecuencias funcionales:** Mensual, Semanal, Quincenal, Anual, Única vez.
8. **Multimoneda:** Montos atados a la moneda de la obligación, pagos con FX dinámico en backend.

## 4. Proposed Data Model
Se requiere una refactorización estructural de la base de datos.
```sql
CREATE TABLE obligations (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    name TEXT NOT NULL,
    category_id UUID,
    currency TEXT NOT NULL,
    type TEXT NOT NULL, -- 'indefinite', 'end_date', 'end_count', 'one_time'
    frequency TEXT NOT NULL, -- 'monthly', 'weekly', 'biweekly', 'yearly', 'one_time'
    payment_mode TEXT NOT NULL, -- 'fixed', 'variable'
    base_amount NUMERIC(15,2),
    start_date DATE NOT NULL,
    first_due_date DATE NOT NULL,
    due_day INT, -- Optional/Complementary
    due_month INT, -- Optional/Complementary
    interval_count INT DEFAULT 1, -- Para saltos específicos si aplica
    end_date DATE,
    end_count INT,
    status TEXT NOT NULL, -- 'active', 'completed', 'archived', 'cancelled'
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE TABLE obligation_periods (
    id UUID PRIMARY KEY,
    obligation_id UUID NOT NULL REFERENCES obligations(id),
    period_key TEXT NOT NULL, -- Ej: '2026-06'
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    due_date DATE NOT NULL,
    amount NUMERIC(15,2), -- NULL si es variable y no se ha definido
    paid_amount NUMERIC(15,2) NOT NULL DEFAULT 0,
    status TEXT NOT NULL, -- Ver Status Model
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
-- Unique constraint (obligation_id, period_key)

CREATE TABLE obligation_payments (
    id UUID PRIMARY KEY,
    obligation_period_id UUID NOT NULL REFERENCES obligation_periods(id),
    account_id UUID REFERENCES accounts(id),
    amount NUMERIC(15,2) NOT NULL, -- En la moneda de la obligación
    source_amount NUMERIC(15,2) NOT NULL, -- En la moneda de la cuenta origen
    source_currency TEXT NOT NULL,
    fx_rate NUMERIC(15,6),
    event_id UUID REFERENCES financial_events(id),
    paid_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

## 5. Entity Definitions
- **Obligation**: Plantilla base. Define la periodicidad, moneda y reglas de finalización.
- **ObligationPeriod**: Instancia concreta de tiempo. Dueño absoluto de su saldo, monto a pagar, y estado.
- **ObligationPayment**: Registro financiero transaccional acoplado directamente a un `ObligationPeriod`.

## 6. Status Model
### Obligation (Root)
- `active`: Generando periodos.
- `completed`: Ya no generará más periodos y todos los pasados están pagados/cerrados.
- `archived` / `cancelled`: Cerrada manualmente.

### ObligationPeriod
- `pending_amount_definition`: Variable, esperando entrada de usuario. No puede recibir abonos ni pagos hasta que se defina el monto.
- `pending_payment`: Monto definido, saldo pendiente, fecha <= due_date.
- `partially_paid`: Monto definido, 0 < paid_amount < amount.
- `paid`: paid_amount >= amount.
- `overdue`: Monto definido, saldo pendiente, fecha > due_date.
- `skipped`: Omitido por el usuario (aplica en V1.6). Solo se puede saltar un periodo no pagado. No cuenta como deuda pendiente, pero cuenta como resuelto para completar la obligación. No borra historial.
- `cancelled`: Anulado.

## 7. Period Generation Rules
- **Lazy Generation:** Al consultar obligaciones de un usuario, el backend evaluará si el periodo actual y el siguiente (`current + 1`) existen en DB. Si no, se insertan.
- **Resolución de fechas (basado en `frequency`):**
  - `monthly`: Usa calendario natural del mes.
  - `yearly`: Usa calendario natural anual.
  - `weekly`: Usa intervalos de 7 días exactos a partir de `first_due_date` o `start_date`.
  - `biweekly`: Usa intervalos de 14 días a partir de `first_due_date`.
  - `one_time`: Usa una única `due_date`.
- **Monto Inicial:** Si `payment_mode == 'fixed'`, copia `base_amount`. Si `payment_mode == 'variable'`, asigna `NULL` y estado `pending_amount_definition`.

## 8. Payment Rules
- **Flujo Variable:** Un periodo en `pending_amount_definition` NO puede recibir pagos. Si se intenta pagar, el sistema arroja `OBLIGATION_PERIOD_AMOUNT_REQUIRED`.
- **General Payment (FIFO):** Endpoint `/obligations/{id}/pay`. El backend busca todos los periodos con saldo pendiente, los ordena por `due_date ASC`, y asigna el dinero en cascada (vencido -> actual -> futuro).
- **FIFO Overpayment:** Si el usuario intenta pagar por FIFO más del saldo restante acumulado total, la transacción se rechaza arrojando `OBLIGATION_PAYMENT_EXCEEDS_REMAINING_BALANCE`. No se crea saldo a favor ni se generan periodos dinámicos.
- **Specific Period Payment:** Endpoint `/obligations/periods/{id}/pay`. El abono entra directamente a ese periodo ignorando deudas pasadas.

## 9. Multi-Currency Rules
- Toda la matemática dentro del dominio `obligations` opera estrictamente en `Obligation.currency`.
- El módulo de pago (`ObligationService`) llamará a los servicios de FX al igual que en V1.5, inyectando la conversión antes de grabar `ObligationPayment`.
- Frontend no requiere cálculos locales.

## 10. Editing Rules
- **Periodos Pasados/Pagados:** Nunca se modifican si ya fueron pagados.
- **Periodo Actual (No Pagado):** Se puede actualizar con confirmación explícita (ej. modificando el monto o saltándolo).
- **Periodos Futuros:** Sí se actualizan automáticamente o explícitamente cuando cambia la plantilla `Obligation`.

## 11. Completion Rules
- **Indefinida:** Nunca entra a `completed` automáticamente.
- **One-time:** Entra a `completed` en cuanto su único periodo quede `paid` o `skipped`.
- **End by Date / Count:** Queda `completed` **solo** cuando todos sus periodos requeridos (hasta cumplir la cuota o fecha) están `paid`, `skipped` o `cancelled` según las reglas. No basta con que solo el último periodo esté pagado.

## 12. API Surface Proposal
- `GET /api/v1/obligations` (Incluye resumen global de deuda extraído desde SQL).
- `POST /api/v1/obligations` (Crea obligación y primer periodo).
- `GET /api/v1/obligations/{id}`
- `GET /api/v1/obligations/{id}/periods` (Historial/futuro).
- `PATCH /api/v1/obligations/periods/{period_id}/amount` (Define monto variable).
- `POST /api/v1/obligations/{id}/pay` (FIFO).
- `POST /api/v1/obligations/periods/{period_id}/pay` (Directo a periodo).

## 13. Migration / Reset Strategy
**Recomendación:** `Reset Limpio`.
Dado que los datos actuales son de prueba y transformar el `paid_this_period` implícito a registros `obligation_periods` normalizados resulta costoso y de escaso valor, se recomienda crear una migración Alembic que elimine todos los registros de `obligation_payments` y `obligations` existentes (`DELETE FROM obligation_payments; DELETE FROM obligations;`), modifique el esquema, y cree las nuevas tablas vacías.

## 14. Test Strategy
- Eliminar la mayoría de `test_obligations_semantics.py` viejo.
- Escribir `test_obligation_period_generator.py` para asegurar que calendarios (meses con 28/30/31 días, años bisiestos) no rompan el generador.
- Escribir `test_obligation_fifo_payment.py` validando la cascada de abonos (vencido -> actual -> futuro).
- Escribir `test_obligation_variable_amounts.py`.

## 15. Implementation Roadmap
1. DB Models & Alembic Reset Migration.
2. Period Generator Engine (Utils & Service).
3. Repositories (Reescritura total de queries Pydantic a SQL nativo).
4. Core Services & FIFO Allocator.
5. FastAPI Routers update.
6. Tests update & OpenAPI generation.

## 16. Risks & Open Questions
- **Riesgos Aceptados:** Ruptura de contratos API con frontend y reescritura masiva de tests de validación semántica en backend.
- **Open Questions:** (Todas resueltas en el "Final Lock").
