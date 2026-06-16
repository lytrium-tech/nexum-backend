# SPRINT 1 — FINANCIAL TRUTH AUDIT

## Estado Actual
El Backend V1 es funcional y sirve de *Single Source of Truth* general, pero actualmente carece de varias capacidades estructurales sobre métricas de "dinero libre" y flexibilidad de saldos:
1. **Cuentas y Balances**: El esquema `AccountCreate` y `app/accounts/service.py` no permiten indicar un saldo inicial. Las cuentas siempre nacen en cero. 
2. **Ajustes de saldo**: No existen eventos formales de `opening_balance` ni `balance_adjustment` en el dominio Cash ni en la base de datos (solo un `manual_adjustment` sin usar).
3. **Snapshot Consistency**: La ruta `/api/v1/intelligence/snapshot` agrega métricas en Python y devuelve `cash`, `cashflow`, `debt`, `goals`, `obligations` y `transfers`. Sin embargo, no expone `free_money`, `safe_money`, `payment_required` ni `committed_outflows`.
4. **Dinero libre fantasma**: Existe un método `get_free_money` en `IntelligenceService` que depende de una vista de SQL antigua y deprecada (`v_financial_snapshot_current_month`), generando un gap técnico de verdad financiera respecto a V1 (que ahora se agrega en Python).
5. **Experiencia Conversacional**: El asistente ignora el concepto de `ask_free_money` (cuánto dinero me sobra realmente) y no soporta intenciones para corregir el saldo si hay descuadres (`balance_adjustment`).

---

## Hallazgos
1. Faltan los tipos de evento `opening_balance` y `balance_adjustment` en la base de datos (`public.financial_events`).
2. Faltan los schemas y lógicas en la API de Cash para registrar balances iniciales o ajustes.
3. El frontend y los usuarios carecen de una vista unificada que reste deudas inminentes (Committed Outflows) del saldo líquido actual.
4. El backend mantiene deuda técnica (`v_financial_snapshot_current_month`) que contradice el nuevo estándar V1 de agregación en Python.

---

## Gaps Identificados
1. **Database**: Faltan los `event_type` requeridos en el constraint de Postgres.
2. **API Accounts**: El endpoint POST `/accounts` debe aceptar `initial_balance` de forma opcional.
3. **API Cash**: Implementar rutas/métodos para `POST /cash/opening-balance` y `POST /cash/adjustment`.
4. **API Snapshot**: Expandir el contrato de `/api/v1/intelligence/snapshot` para devolver el nodo de "verdad":
   - `committed_outflows` (Suma de cuotas/obligaciones pendientes).
   - `payment_required` (Deuda facturada exigible inmediata).
   - `safe_money` / `free_money` (Cash Total - Committed).
5. **Conversational Core**: Actualizar el NLP con herramientas/system prompt para `balance_adjustment` y `ask_free_money`.

---

## Riesgos de Romper V1
- **Ruptura de Contrato de Snapshot**: Si se mutan las propiedades existentes del JSON que devuelve Snapshot, el Frontend fallará. Se debe usar **adición**, es decir, inyectar un nuevo bloque `truth: { ... }` manteniendo intactos `cash`, `debt`, etc.
- **Doble Impacto de Ajustes**: Implementar un `balance_adjustment` sin una matemática impecable puede corromper el saldo contable o duplicar flujos de caja (`cashflow`). Los ajustes **no deben** contar como `income` ni `expense` en el cashflow del mes, de manera similar a las transferencias.
- **Idempotencia**: Al instanciar cuentas con saldo inicial, el evento de cash correspondiente debe atarse lógicamente a la cuenta para que un retry HTTP no inyecte el opening balance dos veces.

---

## Plan de Implementación
1. **Migración de Base de Datos**:
   - Actualizar el `CHECK` constraint de `event_type` en `financial_events` para añadir `opening_balance` y `balance_adjustment`.
   - Deprecar y eliminar vistas obsoletas como `v_financial_snapshot_current_month`.
2. **Dominio Cash y Accounts**:
   - Desarrollar `CashService.register_opening_balance` y `CashService.register_balance_adjustment`.
   - Añadir `initial_balance: Decimal = 0` en `AccountCreate` invocando CashService de forma transaccional.
3. **Dominio Intelligence (Snapshot Consistency)**:
   - Calcular en Python dentro de `IntelligenceService.get_snapshot` los nuevos KPIs (`committed_outflows`, `free_money`, etc.) basándose estrictamente en las piezas ya consultadas (balances, obligations, billed_debt).
   - Expandir el modelo `IntelligenceSnapshotRead` con el nuevo nodo analítico.
4. **Dominio Conversacional**:
   - Ajustar el prompt de sistema del asistente para inyectarle el concepto de `free money / safe money` desde el Snapshot.
   - Añadir las "tools" o intenciones (`create_adjustment`, `ask_free_money`) y enrutarlas correctamente hacia `CashService`.

---

## Orden Recomendado
1. **DB Migrations**: Expandir enums de eventos y eliminar views deprecadas.
2. **API Updates**: Expansión de schemas y services (Accounts & Cash).
3. **Financial Truth Engine**: Actualización de la lógica del Snapshot (`IntelligenceService`).
4. **NLP Expansión**: Integración conversacional (`ConversationsService`).
5. **Regresión y Smoke**: Construcción del smoke de validación.

---

## Tests Necesarios
- **Unitarios**:
  - `test_cash_service.py`: Validación de matemática de ajustes (ej: de 100 a 150 = delta de +50).
  - `test_intelligence_service.py`: Verificación del cálculo estricto de `safe_money` (Cash - Obligations Pendientes - Billed Debt).
  - `test_conversations.py`: Assert de reconocimiento correcto de intents `ask_free_money` y `balance_adjustment`.

---

## Smokes Necesarios
- **`smoke_financial_truth.py`**:
  - Crear cuenta con saldo > 0. Validar que el Ledger y Account reflejen el balance exacto.
  - Ejecutar un `balance_adjustment` API. Validar el delta y que no sume al net_cashflow de income/expense.
  - Simular deudas (Obligaciones). Pedir el Snapshot y validar que el `free_money` cuadre restando las obligaciones del total cash.
  - Conversar con el bot preguntando "¿Cuánto dinero libre tengo?" y validar si la respuesta corresponde con el Snapshot.
