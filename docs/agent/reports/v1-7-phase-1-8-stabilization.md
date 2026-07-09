# Nexum V1.7 — Phase 1.8 Stabilization: Payment Integrity Hardening

## Resumen de Problemas Detectados

Durante las pruebas de QA manual del flujo de pagos V1.7, se detectaron 4 inconsistencias críticas que explicaban el comportamiento anómalo (pagos no reflejados, balances no descontados e inflación de montos restantes):

1. **Race Condition en Lectura de Periodos (Concurrency):**
   `pay_specific_period` y `pay_obligation_fifo` no usaban bloqueos de base de datos (`with_for_update()`). Si la UI enviaba dos peticiones simultáneas (doble click) u ocurría un reintento por red, ambas leían el mismo `paid_amount = 0`, provocando que el último commit sobreescribiera los avances del primero.

2. **Falla Crítica de Idempotencia en Pagos (Idempotency Bug):**
   El código V1.7 registraba el `ObligationPayment` en la sesión y sumaba el `paid_amount` **antes** de procesar el `LedgerEvent`. Si el `LedgerEvent` fallaba por restricción de unicidad (`IntegrityError` en `command_id` del idempotency key), el sistema atrapaba el error y retornaba `idempotent=True`... pero **jamás hacía un rollback** de la sesión.
   Esto causaba que el `ObligationPayment` se guardara duplicado y el `paid_amount` subiera, ¡sin descontar dinero de la cuenta!

3. **Duplicación de Periodos Legacy-V1.7 (Period Key Mismatch):**
   `_create_initial_period` forzaba `YYYY-MM` como `period_key` independientemente de la frecuencia de la obligación (ej. para `one_time`). Cuando las tareas legacy en background o endpoints viejos llamaban a `sync_periods`, no encontraban su llave esperada (`ONE-TIME`) y creaban un **segundo** periodo paralelo por el monto original ($10.000). Al pagarse el periodo original V1.7 y cerrarse (`paid`), éste desaparecía del frontend, dejando visible solo el periodo duplicado y mostrando "resto por pagar: $10.000".

4. **Falta de Linkeo Financiero (Financial Event FK):**
   Los pagos creados en V1.7 no estaban guardando el `financial_event_id` en `ObligationPayment`, rompiendo el vínculo directo entre el pago de la obligación y la transacción en el ledger.

## Soluciones Implementadas (`app/obligations/service_v17.py`)

1. **Locking Transaccional (`with_for_update()`):**
   Se implementó `with_for_update()` en la obtención del `ObligationPeriod` tanto en pagos específicos como en la estrategia FIFO, asegurando que los pagos concurrentes se formen en fila y acumulen correctamente el `paid_amount`.

2. **Rediseño del Flujo Idempotente:**
   Se movió el cálculo y creación del `LedgerEvent` al principio de la transacción. Si retorna `idempotent=True`, se hace `await self.session.rollback()` para limpiar cualquier estado sucio y se retorna el pago ya existente, previniendo duplicaciones fantasma y balances asimétricos.

3. **Sincronización de Period Keys:**
   Se eliminó la generación manual de `YYYY-MM` en `_create_initial_period` y se sustituyó por `generate_period_key` y `calculate_period_bounds` del `period_engine`. Ahora V1.7 y Legacy generan exactamente la misma huella en la BD.

4. **Linkeo de FKs Completado:**
   Ahora todo `ObligationPayment` incluye su respectivo `financial_event_id` al momento de persistirse.

## Siguientes Pasos
Se solicita **aprobación** para realizar el commit y el push de estos cambios. Una vez desplegado, el flujo V1.7 será robusto ante reintentos de red y concurrencia.
