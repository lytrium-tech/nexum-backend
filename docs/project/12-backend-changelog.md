# 12 — Backend Changelog

Este changelog captura hitos arquitectónicos y funcionales clave (omitir parches menores):

## V1.2 - V1.4
- Implementaciones fundacionales (Accounts, Users, Ledger).
- Primeras iteraciones de Goals (Meta de ahorro).

## V1.5
- **Objetivo:** Motores de Tarjetas de Crédito.
- **Capacidades Añadidas:** Generación de Statements, modelado complejo de deuda rotativa y cuotas, estimación de intereses.
- **Estado Actual:** Reemplazado y canónicamente cerrado por Credit Domain V2.

## V1.6
- **Objetivo:** Introducción inicial de Obligations Core.
- **Capacidades Añadidas:** Abstracción de pasivos, deuda como plantillas, y el motor base de períodos (`PeriodEngine`).

## V1.6.1 & V1.6.2
- **Objetivo:** Correcciones y estabilización (Intelligence Context).
- **Problemas resueltos:** Diagnostics, resolución de fallas de agregación (Snapshot diagnosis).

## V1.7
- **Objetivo:** Estabilizar y hacer transaccionalmente seguro el Core de Obligaciones.
- **Capacidades Añadidas:**
  - `ObligationPeriod` lifecycle formalizado (pending, overdue, paid).
  - Cross-currency Payments y el motor **FX Rate Snapshot**.
  - Feature Flags (`NEXUM_OBLIGATIONS_V17_ENABLED`).
- **Estado Actual:** Es la versión base operativa. Todas las rutas de pago y consultas están bajo `/api/v1.7/`.

## Iteración Funcional "V1.8"
- **Objetivo:** Mejoras operativas y UX del lado backend sobre V1.7.
- **Capacidades Añadidas:**
  - **Smart One-Button Payment:** Delegación al backend del monto total sin requerir selección de período específico.
  - **FIFO Avanzado:** Cobertura de periodos vencidos y pagos parciales idempotentes (slices).
  - **Auto-refresh / Batch refresh:** Actualización silenciosa de estados de periodos vencidos en lecturas.
  - **Overview Endpoint:** Unificación analítica de pasivos alineada con el ORM.
  - **FX Bidireccional:** Soporte nativo para COP ↔ USD invertido en la base matemática de snapshot.
  - **Aclaración:** Absorbió el modelo anterior pero no creó una versión `/api/v1.8/` paralela de API.

## Credit Domain V2 (Canonical Closure)
- **Objetivo:** Cierre canónico integral del dominio de Tarjetas de Crédito (`app/credit`) resolviendo la totalidad de los hallazgos de Goal 1 (F-01 a F-19).
- **Capacidades Añadidas:**
  - **Partición Canónica:** Frontera automática de activación sin sintetizar historia legacy ni reinterpretar `amount`.
  - **Ciclos y Fechas Canónicas:** Asignación de compras post-corte al ciclo subsiguiente; hora canónica anclada a `financial_date()` (`America/Bogota`).
  - **Extractos Inmutables:** Sub-sistema de `credit_card_statements` y `credit_card_statement_components` con protección de inmutabilidad vía triggers PostgreSQL (`trg_cc_statements_immutable`). Aritmética de arrastre temporal (`prior_balance` y `prior_credit`).
  - **Trazabilidad de Pagos:** Creación de tabla junction `credit_card_payment_allocations` vinculando pagos con cuotas y cargos en orden de waterfall determinista.
  - **Invariante de Metas (F-13):** Enforzamiento estricto bajo lock de reservas de fondos de Metas (`account.balance - reserved >= payment_amount`).
  - **Jerarquía Global de Bloqueo (F-14):** Orden de locking `Account -> CreditCard -> DebtComponents -> Allocations`, eliminando ciclos de deadlock. Endpoint de preview 100% read-only y sin locks.
  - **Ajustes y Reversos (F-09):** Soporte integral de reembolsos (`refund`), disputas (`chargeback`), créditos de extracto y reversos completos de pagos (`reversal`).
  - **Idempotencia Profunda (F-16):** Verificación multidimensional de `command_id` contra usuario, operación, entidad, cuenta y montos.
  - **Consumidores Canónicos:** Sincronización de Intelligence y Conversaciones para consumir hechos canónicos reales (`get_credit_summary`), desacoplándose de vistas obsoletas y columnas muertas.
  - **Certificación física:** Alembic A/B/C verificado desde vacío, head previo y dataset legacy representativo; CT101 PostgreSQL 17 ejecutó 29/29 casos Credit. F-13, F-14, F-15, F-16 y F-19 cerrados físicamente. La migración forward final `credit_v2_refund_type` habilita refund con downgrade fail-closed si existen hechos refund.
  - **Límite del handoff:** La certificación entrega evidencia para revisión Founder. No constituye autorización automática de Frontend Sync/QA ni cambia frontend o producción.

---
*Last verified against Credit Goal 2 final implementation (`feature/credit-canonical-closure`)*
- Integrado con [Wallets V1](./13-accounts-wallets.md) y [Credit V2 Handoff](../handoff/backend/credit-canonical-v2-frontend-handoff.md).
