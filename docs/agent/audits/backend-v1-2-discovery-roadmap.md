# Backend V1.2 Discovery & Roadmap

## 1. Recommended Version

La recomendación oficial es proceder con **Backend V1.2**, no Backend V2.
El sistema requiere estabilizar métricas, periodos y aislar monedas de forma pasiva antes de lanzar la Alpha. Friend Loans quedan para V2. Advanced financial guidance queda para V2. FX real queda para V2 o posterior. Account archiving sí entra en V1.2. Database cleanup y views pruning entran como Sprint 0.

## 2. Executive Summary

El proceso de alineación Frontend-Backend V1.1 expuso discrepancias severas en la forma en que el backend totaliza el dinero y el flujo de caja, y cómo se combinan las monedas. Backend V1.2 corregirá estos problemas estructurales e iniciará con un Sprint 0 de limpieza exhaustiva de base de datos y vistas para certificar la plataforma como Alpha Ready real.

## 3. Evidence Reviewed

- `../docs/handoff/frontend/frontend-to-backend.md` (Frontend Findings)
- `docs/context/backend-current-state.md`
- `docs/context/nexum-backend-v1-1-roadmap.txt`
- `docs/project/03-api-contracts.md`
- `docs/project/11-frontend-handoff.md`
- `openapi.json`
- Decisiones del Fundador (Pruning, Archiving, Multi-moneda, Loans, Chat).

## 4. Findings Classification

1. **Tarjetas de crédito (cutoff, due_day, intereses, cuotas, pago)**
   - Clasificación: PRODUCT DECISION / BACKEND CALCULATION ISSUE
   - Prioridad: HIGH
   - Dominio: backend

2. **Metas (estado pendiente, redondeo, ahorro diario)**
   - Clasificación: BACKEND CALCULATION ISSUE
   - Prioridad: HIGH
   - Dominio: backend

3. **Obligaciones (reactivación, vencimiento, doble deducción)**
   - Clasificación: BACKEND CALCULATION ISSUE
   - Prioridad: HIGH
   - Dominio: backend

4. **Snapshot / Dashboard (separar métricas mensuales de históricos)**
   - Clasificación: BUG / CONTRACT GAP
   - Prioridad: BLOCKER
   - Dominio: backend

5. **Pago de tarjeta (debt_payment vs gasto)**
   - Clasificación: BUG / BACKEND CALCULATION ISSUE
   - Prioridad: BLOCKER
   - Dominio: backend

6. **Transferencias / préstamos (ledger único)**
   - Clasificación: OUT OF SCOPE V1.2 (Préstamos a V2)
   - Prioridad: LOW
   - Dominio: shared

7. **Billeteras multi-moneda (unidad mínima, no mezclar)**
   - Clasificación: BUG / BACKEND CALCULATION ISSUE
   - Prioridad: BLOCKER
   - Dominio: backend

8. **Eliminación / archivado (billeteras/categorías)**
   - Clasificación: DATA MODEL ISSUE
   - Prioridad: HIGH
   - Dominio: backend

9. **Chat financiero (contexto, multi-intent, prudent advice)**
   - Clasificación: OUT OF SCOPE V1.2 (solo auditoría de safety en V1.2)
   - Prioridad: MEDIUM
   - Dominio: shared

10. **Sección Nuevo (creación de UI)**
    - Clasificación: PRODUCT DECISION / FRONTEND FOLLOW-UP
    - Prioridad: OUT OF SCOPE V1.2 (Backend solo valida endpoints)
    - Dominio: frontend

11. **Limpieza de Base de Datos y Vistas**
    - Clasificación: BACKEND MAINTENANCE
    - Prioridad: BLOCKER (Sprint 0)
    - Dominio: backend

## 5. Backend V1.2 Recommended Scope

- **Database & Views Cleanup (Sprint 0)**: Limpieza segura de datos de prueba y poda de vistas obsoletas.
- **Snapshot Period Semantics**: Separación absoluta de métricas históricas vs. métricas del periodo (mes) actual.
- **Cashflow Types**: Tipificación en el ledger para no duplicar `credit_card_consumption` con `debt_payment`.
- **Passive Multi-Currency**: Soporte pasivo de monedas (sin FX real).
- **Goal Rounding**: Redondeo dinámico por unidad monetaria mínima (COP 50, USD 0.01, EUR 0.01).
- **Obligations Period Reactivation**: Lógica segura de periodos recurrentes.
- **Archive Strategy**: Implementar `is_active` en cuentas y categorías (soft-delete). Hard delete bloqueado si hay historial.
- **Chat Context Audit**: Auditoría de límites y safety del prompt, posponiendo orientación financiera prudente a V2.

## 6. Out Of Scope For V1.2

- **Backend V2 features completas**.
- Modelado activo de Multi-Moneda (FX en tiempo real automático queda para V2 o posterior).
- Préstamos entre amigos completos (Friend Loans quedan para V2).
- Orientación prudente financiera avanzada en Chat (queda para V2).
- Diseño de UI para la Sección Nuevo (Frontend).

## 7. Product Decisions Needed

1. **Multi-moneda en Dashboard**: ¿Se debe exigir un parámetro `currency` en el Snapshot para que devuelva la información de una sola moneda, o devolvemos todo segmentado en un diccionario `totals_by_currency`?

## 8. Contract Changes Needed

- `IntelligenceSnapshotRead`: Mover variables históricas a un nodo `historical` e introducir métricas del periodo en `current_period`.
- `GoalRead`: Agregar `daily_required_this_period`.
- `Account`/`Category`: Agregar propiedad `is_active` o endpoint de archivo.

## 9. Snapshot Monthly Semantics

Backend V1.2 debe exponer de forma aislada las métricas del periodo:
- `income_current_period`
- `cash_expenses_current_period`
- `credit_card_consumption_current_period`
- `debt_payments_current_period`
- `committed_outflows_current_period`
- `net_cashflow_current_period`

## 10. Credit Card Semantics

El pago de una tarjeta de crédito **debe** procesarse como `debt_payment`. Debe reducir `available_real` y reducir `current_debt`. No debe registrarse como `expense` operativo duplicado.

## 11. Goals Period Semantics

Las respuestas de la API (`monthly_required`, `required_this_period`, `remaining_required_this_period`) deben redondearse al alza según la unidad mínima de la moneda:
- COP → unidad mínima 50
- USD → unidad mínima 0.01
- EUR → unidad mínima 0.01

## 12. Multi-Currency Strategy

V1.2 implementará multi-moneda pasiva. No se implementará motor FX en V1.2.
Reglas:
- Un usuario puede tener cuentas/billeteras en COP, USD, EUR u otra moneda.
- Cada cuenta conserva su currency.
- Backend no debe sumar saldos de monedas distintas sin conversión explícita.
- Cada moneda tiene su unidad mínima de redondeo.

## 13. Obligations Period Semantics

Las obligaciones mensuales o recurrentes deben recalcular de forma correcta su estado `period_status`. El backend debe evitar dobles cobros o deducciones duplicadas asegurando trazabilidad por periodo.

## 14. Transfers And Friend Loans

Se mantiene el ledger único para transferencias internas. No implementar préstamos entre amigos en V1.2. Solo se deja diseño compatible (transferencias) sin módulo real.

## 15. Archive/Delete Strategy

Cuenta/billetera con historial financiero no debe eliminarse físicamente.
- Debe poder archivarse/desactivarse.
- Hard delete solo se permite si no tiene dependencias y es completamente seguro.

## 16. Chat Financial Advisor Scope

V1.2 solo debe auditar:
- prompt actual
- contexto disponible
- límites del chat
- uso de snapshot/history/goals/obligations/credit
- riesgos de consejo financiero
La orientación prudente conversacional avanzada no se implementará en V1.2.

## 17. Suggested Sprint Roadmap

- **Sprint 0 — Database Cleanup & Views Pruning**
- **Sprint 1 — Snapshot Period Semantics & Cashflow Subtypes**
- **Sprint 2 — Passive Multi-Currency & Currency Minimum Units**
- **Sprint 3 — Goals Period Semantics & Rounding**
- **Sprint 4 — Obligations Period Lifecycle & Account Archiving**
- **Sprint 5 — Credit Card Cycle Semantics**
- **Sprint 6 — Chat Context Audit & Prompt Safety**
- **Sprint 7 — Backend V1.2 Regression & Alpha Recheck**

## 18. Sprint 0 Details (Cleanup & Pruning)

**Database Cleanup:**
- Limpiar datos de prueba (agentes/smokes).
- Incluir inventario de tablas, conteo de filas, dependencias FK, y datos (usuarios, finanzas, logs, ai_runs, pending_actions, financial_events, cuentas, categorías, metas, obligaciones, tarjetas, transferencias, installments).
- Proponer: backup preventivo, plan SQL, orden de limpieza, verificación posterior, y reseed mínimo si aplica.

**Views Pruning:**
- Identificar vistas usadas por backend, endpoints, smokes/tests, documentadas para frontend, auxiliares, legacy de n8n, y sin dependencias.
- Proponer eliminación segura de vistas obsoletas.
- Clasificar vistas en: `KEEP`, `KEEP_FOR_NOW`, `DEPRECATE`, `DROP_CANDIDATE`, `UNKNOWN_NEEDS_REVIEW`.

## 19. Test Plan

Unit Tests enfocados en la no-mezcla de divisas y redondeos. Verificaciones de soft-delete de cuentas. Pruebas de limpieza segura sin corromper base de datos para Sprint 0.

## 20. Risks

Riesgos en la limpieza de la base de datos (Sprint 0) que requieran un reseed manual de datos críticos.

## 21. Next Step

Aprobar formalmente este Roadmap e instruir el inicio del **Sprint 0 — Database Cleanup & Views Pruning**.
