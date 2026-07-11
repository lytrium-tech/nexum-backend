# 10 — Frontend Handoff Contracts

## Separación de Responsabilidades
El backend es desplegable independientemente del frontend. La responsabilidad de configurar la ruta base (ej. `NEXT_PUBLIC_API_BASE_URL`) recae en el despliegue del frontend, no en la configuración del backend.

## Flujo de Pago de Obligaciones (V1.7)
El contrato principal esperado por el backend para pagos (`/api/v1.7/obligations/{id}/payments/smart` o pagos específicos) requiere:
- `amount`: El monto en la moneda destino de la deuda.
- `source_account_id`: UUID de la cuenta de fondeo.
- `rate_snapshot_id`: (Opcional, pero **obligatorio** en pagos cross-currency actuales) UUID del snapshot obtenido en `/api/v1.7/fx/rates/latest`.

### Responsabilidades del Frontend
- **NO envía `source_amount`:** El frontend no determina cuánto se debita finalmente de la cuenta de fondeo. (Aunque existe en el esquema JSON por compatibilidad legacy, es ignorado por la lógica transaccional actual).
- **NO envía `quote_id`:** Aunque soportado opcionalmente por retro-compatibilidad, el flujo actual usa `rate_snapshot_id`.
- **NO consume DólarAPI directamente:** El frontend consume el endpoint del backend para obtener el snapshot de la tasa.

### Casos de Uso
1. **Cross-Currency (USD a COP o COP a USD):** Requiere consultar FX Snapshot previamente y adjuntar el `rate_snapshot_id` en el payload de pago.
2. **Same-Currency (COP a COP):** No requiere snapshot. El backend asume tasa 1 internamente y procesa el débito.

## Idempotencia y Manejo de Errores
- El frontend debe enviar un `Idempotency-Key` único (si está expuesto) o manejar el estado de carga para prevenir dobles envíos accidentales.
- Si un snapshot expira, el backend retorna un error estructurado 400 (`Expired snapshot` o similar). El frontend debe estar preparado para recuperar un nuevo snapshot y reintentar la transacción.

## Capacidades de Consumo
- **Overview:** `/api/v1.7/obligations/overview` unifica el estado consolidado de obligaciones.
- **Summary:** Endpoints específicos para obtener resúmenes cuantitativos.
- **Intelligence Context:** Retorna un análisis en texto rico que puede ser alimentado directamente a la IA.

## Compatibilidad Legacy Temporal
El backend mantiene endpoints de preview legacy o permite opcionalmente campos antiguos (`source_amount`, `quote_id`) puramente para evitar romper versiones antiguas de clientes en desarrollo. Sin embargo, no deben ser utilizados en nuevas integraciones.

---
*Last verified against `367ebdc`*
