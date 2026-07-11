# Nexum V1.7 / V1.8 (Funcional) — Frontend Sync Handoff

> **HISTORICAL NOTICE:** Este documento conserva instrucciones históricas de sincronización, pero ha sido actualizado para reflejar los contratos finales de `FX Snapshot` y `Smart Payment`. Para el contrato técnico oficial, consulte [10-frontend-handoff-contracts.md](../../project/10-frontend-handoff-contracts.md).

## 1. Contexto Operativo Real

El objetivo de esta fase es sincronizar el frontend local con las nuevas capacidades del backend V1.7 y la iteración funcional V1.8. **No existe un deploy de frontend en producción**, ni se utiliza Vercel. El dominio público actualmente no sirve una aplicación frontend.

### Estado del Backend (Producción / VPS)
* **Despliegue Independiente:** El backend es desplegable independientemente del frontend.
* **API Pública:** `https://api.nexum.lytrium.tech`
* **Feature Flag:** `NEXUM_OBLIGATIONS_V17_ENABLED` (temporalmente protege las rutas con `403` si está apagado).

### Estado del Frontend (Local)
* **Ejecución:** Se levanta usando `pnpm dev` en local, apuntando a la API de producción.
* **Deploy:** **PROHIBIDO**. No hacer deploy, no configurar Vercel, no tocar producción.

---

## 2. Novedades y Contratos (V1.7 / V1.8)

El backend expone funcionalidades maduras de Obligations y FX:

### FX Rate Snapshot y Pagos
* El frontend **actual** ya no requiere ni debe calcular el `source_amount`.
* El frontend **no usa** `quote_id` en el flujo principal.
* **Flujo esperado:** El frontend consume `/api/v1.7/fx/rates/latest` para obtener un `rate_snapshot_id`, y lo adjunta al payload de pago.
* **Cross-currency (USD/COP):** Requiere `rate_snapshot_id`. El backend calcula la deducción exacta de la cuenta de fondeo.
* **Same-currency:** No requiere snapshot.
* **Compatibilidad Legacy:** El backend aún soporta *opcionalmente* `source_amount` y `quote_id` por retro-compatibilidad, pero no deben usarse en el nuevo flujo.

### Smart Payment, FIFO y Overview
Bajo las mismas rutas `/api/v1.7`, el backend ahora soporta:
* **Smart One-Button Payment:** Delegación completa de pagos sin elegir período específico (`/api/v1.7/obligations/{id}/payments/smart`).
* **FIFO Automático:** El backend distribuye el dinero cronológicamente, incluso en períodos vencidos (slices idempotentes).
* **Overview:** Endpoint consolidado `/api/v1.7/obligations/overview`.

---

## 3. Configuración Local del Frontend

El frontend debe apuntar a la API de producción e implementar feature flags locales para aislar V1.7/V1.8.

### Variables de Entorno Locales (`.env.local`)
1. **API Base URL:**
   ```env
   NEXT_PUBLIC_API_BASE_URL=https://api.nexum.lytrium.tech
   ```
2. **Feature Flag Local:**
   ```env
   NEXT_PUBLIC_NEXUM_OBLIGATIONS_V17_ENABLED=true
   ```

---

## 4. Reglas Estrictas para el Frontend

### El Frontend DEBE:
* Correr exclusivamente de forma local (`pnpm dev`).
* Consumir los datos desde `https://api.nexum.lytrium.tech`.
* **Representar información:** El backend calcula, el frontend dibuja.
* Consumir y enviar el `rate_snapshot_id`.
* Manejar `403` y `401` de forma grácil.

### El Frontend NO DEBE:
* Hacer deploy (ni local ni en pipelines).
* Realizar cálculos financieros o deducir conversiones sin consultar el backend.
* Enviar `source_amount` forzado al backend.
* Llamar a DólarAPI directamente (todo pasa por el backend).

---
*Documento actualizado y alineado con el estado canónico de backend.*

