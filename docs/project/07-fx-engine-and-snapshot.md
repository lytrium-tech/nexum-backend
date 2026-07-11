# 07 — FX Engine and Rate Snapshot

## El Problema de la Volatilidad
En ecosistemas multi-divisa (USD, COP), las tasas de cambio fluctúan. Confiar ciegamente en una tasa consultada en tiempo real al momento del pago genera el riesgo de desajustes transaccionales (el usuario ve una tasa en pantalla, pero un milisegundo después el backend debita otra).

## La Solución: FX Rate Snapshot
El motor de FX en Nexum estabiliza la tasa a través de Snapshots persistidos.

### Flujo Operativo:
1. El frontend/cliente consulta el endpoint `/api/v1.7/fx/rates/latest?from_currency=USD&to_currency=COP`.
2. El backend (consultando a **DólarAPI Colombia** en producción o a un **Proveedor Estático** en entorno local) obtiene la tasa.
3. El backend persiste esta tasa en la tabla `exchange_rates` y le asigna un TTL (expiración). Se retorna un `rate_snapshot_id` al frontend, junto con la tasa y la vigencia.
4. Cuando el usuario confirma el pago, envía su `amount` en la moneda destino, la cuenta de origen (`source_account_id`), y el `rate_snapshot_id`.
5. **Backend Calculates:** El backend verifica que el snapshot existe, no ha expirado y coincide con las monedas esperadas. Luego, realiza el cálculo transaccional oficial usando `Decimal` y debitando el `source_amount` exacto a la cuenta de origen.

## Reglas Matemáticas y Bidireccionalidad
- **USD → COP:** Tasa directa. `source_amount = payment_amount / rate`.
- **COP → USD:** El sistema soporta operaciones inversas con el mismo motor de snapshots invirtiendo matemáticamente la tasa.
- **Same-Currency (COP → COP):** El flujo puentea el FX Engine internamente asumiendo una tasa virtual de `1.0`. No se requiere snapshot.

## Contratos y Transición Legacy
Actualmente, el sistema está en un estado transitorio diseñado para no quebrar clientes móviles antiguos.
- El frontend **actual** ya no decide ni envía el `source_amount` al procesar un pago. Se apoya en enviar `rate_snapshot_id`.
- Los campos `source_amount` y el antiguo `quote_id` siguen existiendo de forma **opcional** en los contratos HTTP (ej. `openapi.json`) por compatibilidad legacy.
- Existen endpoints de *preview legacy* aún activos para sostener calculadoras visuales del frontend antiguo, aunque el backend asume la total autoridad del débito final a nivel transaccional.

---
*Last verified against `367ebdc`*
