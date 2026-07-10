# NEXUM V1.7 — OBLIGATION ACTIONS & AUTO-REFRESH POLISH (PHASE 5B.3)

## 1. Contexto y Objetivos

Después de validar el core financiero de pagos FIFO (Phase 5B.2), se identificó que el usuario dependía de un botón manual "Actualizar vencimientos" para avanzar el estado de los periodos (`pending_payment` -> `overdue`) y generar periodos nuevos.
Además, la visibilidad de los botones "Pagar obligación" (FIFO) y "Pagar periodo" necesitaba ajustarse a reglas de negocio semánticas claras (e.g. no sugerir FIFO si solo hay un periodo próximo pendiente).

## 2. Cambios Implementados

### Backend (Auto-Refresh)
Se integró el flujo de actualización automática (`refresh_overdue_periods`) de manera idempotente y tolerante a fallos en el ciclo de vida de Obligaciones V1.7 (`app/obligations/service_v17.py`):
1. **Listado / Summary**: Se itera sobre todas las obligaciones activas del usuario (usando su `obligation_id`) y se corre el auto-refresh silenciosamente antes de calcular el Dashboard/Resumen.
2. **Listado de Periodos**: Al entrar al detalle de una obligación (`list_periods_for_obligation`), el sistema asegura generar periodos faltantes y marcar los atrasados de forma automática.
3. **Definición de Montos**: Se incluyó el refresco de los periodos al usar `define_period_amount`.
4. **Creación de Obligación**: Tras crear una nueva obligación y persistir el periodo inicial (`create_obligation_v17`), se hace una llamada a `refresh_overdue_periods` para adelantar vencimientos (si `start_date` era en el pasado).

### Frontend (UI Polish)
Se modificó `src/app/app/obligations/ObligationsV17Client.tsx`:
1. **Actualizar vencimientos**: Se eliminó el botón manual y el estado `syncing` subyacente.
2. **Pagar Obligación (FIFO)**: Se incluyó un helper `canShowPayObligation` que verifica si hay más de 2 periodos a pagar (de cualquier tipo) o si hay al menos un periodo `overdue`. De esta manera se omite para periodos próximos únicos pendientes.
3. **Pagar Periodo**: Se condicionó el botón al estado de pago y se añadió explícitamente que el saldo por pagar sea mayor a 0 (`amount_due > 0`). Si el saldo es 0 pero sigue expuesto al frontend, muestra "Sin saldo por pagar".
4. **Semántica Visual**: Se agregó la función `getPeriodLabel` que designa a los periodos futuros con `pending_payment` como "Próximo Periodo" (en vez de "Periodo Actual"), además de "Periodo Vencido", etc.

## 3. Estado Final

- Backend: Los endpoints y procesos de Obligations V1.7 garantizan estado adelantado de periodos para lecturas críticas y acciones asíncronas.
- Frontend: `pnpm lint` y `pnpm build` sin errores tras limpiar estados muertos y directivas inútiles.
- **Requiere**: Pruebas manuales (QA) por parte de Steven sobre la transición de UI al cambiar fechas y revisar nombres de periodos reales antes de dar por **estable** a V1.7 en producción.

## 4. Riesgos o Notas
- `get_summary` realiza loops SQL sobre las obligaciones activas. En un usuario con gran cantidad de obligaciones activas simultáneas, la latencia podría subir marginalmente.

## 5. Siguientes Pasos
- Commit del UI y backend push a VPS con la autorización de Steven.
- Declaración de V1.7 Estable una vez superada la revisión.
