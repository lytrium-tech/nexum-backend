# NEXUM V1.8 — OBLIGATIONS PERFORMANCE POLISH + BIDIRECTIONAL FX FIX

## 1. N+1 Periods Status
El patrón N+1 de periodos fue definitivamente eliminado en la pantalla principal usando el endpoint batch de `overview`.

## 2. Duplicate Overview/Summary Cause
La doble ejecución de `getObligationsOverviewV17Action` y `getObligationsSummaryV17Action` observada en los logs del frontend local se debe exclusivamente al **React Strict Mode** en desarrollo (`REACT_STRICT_MODE_DOUBLE_EFFECT`). En modo desarrollo, React ejecuta el ciclo `mount -> effect -> unmount -> mount -> effect` para asegurar que los hooks sean idempotentes. Dado que `fetchAllData(true)` está atado a un `useEffect` en mount, se dispara dos veces.

## 3. Performance Fix
- No se implementó ningún workaround tipo *ref guard* feo ya que este es el comportamiento correcto de React en `dev`.
- En un build de producción (`next build`), el efecto solo se ejecuta una vez.
- **Endpoint Screen:** No se recomienda crear el endpoint `/api/v1.7/obligations/screen` agrupador porque actualmente se utiliza `Promise.all` para paralelizar `overview` y `summary`, lo que ya es óptimo y mantiene responsabilidades separadas.

## 4. FX Bidireccional: Root Cause
El error que bloqueaba el pago de una Obligación en USD con una Cuenta en COP se debía a que los proveedores de FX (`StaticFxRateProvider` y `DolarApiColombiaFxRateProvider`) solo tenían configurada la dirección `USD -> COP`. Al pedir explícitamente el snapshot inverso (`COP -> USD`), retornaban `None`, lo cual resultaba en un error "FX rate unavailable" propagado hasta el frontend, evitando que se muestre el preview.

El backend en su capa de pago *smart/FIFO* (`source_amount = amount / fx_rate`) maneja perfectamente cualquier dirección matemática siempre que la tasa se entregue correctamente.

## 5. FX Bidireccional: Fix Backend
- Se modificaron ambos proveedores de FX en `app/fx/provider.py`.
- Si se solicita `COP -> USD`, el proveedor ahora obtiene la tasa `USD -> COP` y devuelve la inversa matemática: `1 / rate`.
- Ahora el endpoint de cotización y snapshot devuelve tasas para ambas direcciones.

## 6. Siguientes Pasos
- El backend debe commitearse y desplegarse.
- Frontend sigue limpio y no requiere arreglos para ninguno de estos dos puntos.
- Obligations V1.8 está funcional y performante.
