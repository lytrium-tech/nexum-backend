# 12 — Backend Changelog

Este changelog captura hitos arquitectónicos y funcionales clave (omitir parches menores):

## V1.2 - V1.4
- Implementaciones fundacionales (Accounts, Users, Ledger).
- Primeras iteraciones de Goals (Meta de ahorro).

## V1.5
- **Objetivo:** Motores de Tarjetas de Crédito.
- **Capacidades Añadidas:** Generación de Statements, modelado complejo de deuda rotativa y cuotas, estimación de intereses.
- **Estado Actual:** Absorbido como el modelo operativo principal de tarjetas.

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

---
*Last verified against `367ebdc`*