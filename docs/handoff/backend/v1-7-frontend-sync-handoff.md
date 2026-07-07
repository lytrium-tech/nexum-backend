# Nexum V1.7 — Frontend Sync Handoff

## 1. Contexto Operativo Real

El objetivo de esta fase es sincronizar el frontend local con las nuevas capacidades del backend V1.7. **No existe un deploy de frontend en producción**, ni se utiliza Vercel. El dominio público actualmente no sirve una aplicación frontend.

### Estado del Backend (Producción / VPS)
* **Despliegue:** El backend V1.7 está **completamente desplegado** y corriendo en el VPS de producción.
* **API Pública:** `https://api.nexum.lytrium.tech`
* **Base de Datos:** Migrada a `v1_7_phase2_1 (head)`.
* **Feature Flag en VPS:** `NEXUM_OBLIGATIONS_V17_ENABLED=false`
* **Salud:** El servicio está healthy y respondiendo correctamente.

### Estado del Frontend (Local)
* **Ubicación:** Entorno puramente local.
* **Ejecución:** Se levanta usando `pnpm dev`.
* **Deploy:** **PROHIBIDO**. No hacer deploy, no configurar Vercel, no tocar producción.

---

## 2. Novedades en Backend V1.7

El backend expone ahora funcionalidades de Obligations V1.7:
* Soporte para manejo de FX (Foreign Exchange).
* Persistencia de FX Quotes.
* Integración con Ledger y Financial Events.
* Endpoints específicos para resumen y contexto de obligaciones.
* Hardening de la idempotencia en transacciones.

### Endpoints Relevantes

El frontend debe estar preparado para consumir los siguientes nuevos endpoints:

* `GET /api/v1.7/obligations/summary`
* `GET /api/v1.7/obligations/intelligence-context`

**Nota Crítica de Auth y Flags:**
Dado que el flag del backend (`NEXUM_OBLIGATIONS_V17_ENABLED`) está temporalmente en `false` en producción, las llamadas a estos endpoints pueden devolver un `403 Feature Disabled` (o comportamiento equivalente). Si falta token de autenticación, devolverán `401 Unauthorized`. El frontend debe manejar estos estados de forma elegante sin romper la UI.

---

## 3. Configuración Local del Frontend

El frontend debe apuntar a la API de producción e implementar feature flags locales para aislar V1.7.

### Variables de Entorno Locales (`.env.local`)

1. **API Base URL:**
   ```env
   NEXT_PUBLIC_API_BASE_URL=https://api.nexum.lytrium.tech
   ```
   *Esta URL apunta al VPS donde ya reside el backend V1.7.*

2. **Feature Flag V1.7:**
   ```env
   NEXT_PUBLIC_NEXUM_OBLIGATIONS_V17_ENABLED=false
   ```
   * **`false`**: Oculta completamente la UI/cards de V1.7. Mantiene la experiencia intacta para V1.5.
   * **`true`**: Activa la UI de V1.7 para pruebas exclusivas en entorno local.

---

## 4. Reglas Estrictas para el Frontend

### El Frontend DEBE:
* Correr exclusivamente de forma local (`pnpm dev`).
* Consumir los datos desde `https://api.nexum.lytrium.tech`.
* **Representar información:** El backend calcula, el frontend dibuja.
* Mantener la experiencia de V1.5 totalmente estable.
* Aislar toda la implementación nueva (V1.7) detrás de `NEXT_PUBLIC_NEXUM_OBLIGATIONS_V17_ENABLED`.
* Manejar `403` y `401` de forma grácil para endpoints V1.7, renderizando *Empty States* o *Error States* sin colapsar la aplicación.

### El Frontend NO DEBE:
* Hacer deploy (ni local ni en pipelines).
* Configurar, invocar o requerir tokens de Vercel.
* Tocar, mutar, o alterar la configuración del Backend / VPS.
* Cambiar contratos de la API o alterar la BD.
* Realizar cálculos financieros en el cliente.
* Sumar cantidades en monedas mixtas sin conversión dictada por backend.
* Asumir que `nexum.lytrium.tech` sirve el frontend.

---

## 5. Plan de Pruebas Locales (Validación)

El equipo/agente frontend debe validar localmente los siguientes puntos:

1. **Build y Linter:** 
   * Ejecutar `pnpm install`, `pnpm lint`, `pnpm build` localmente y verificar que todo pasa sin errores críticos.
2. **Arranque:** 
   * Iniciar la app con `pnpm dev`.
3. **Flujo V1.5 (Flag OFF):** 
   * Con `NEXT_PUBLIC_NEXUM_OBLIGATIONS_V17_ENABLED=false`, validar que la tarjeta de V1.7 está oculta.
   * Confirmar que el Dashboard y flujos actuales siguen funcionando.
   * Verificar que no existan llamadas indeseadas a `/api/v1.7/` en la pestaña Network.
4. **Flujo V1.7 (Flag ON):** 
   * Cambiar flag a `true` en `.env.local` y reiniciar `pnpm dev`.
   * Verificar que la UI de V1.7 aparece.
   * Validar el correcto manejo de errores si la API responde `403` o `401`.

**Conclusión:** 
"Backend V1.7 ya está listo en VPS. Haz sync del frontend local con backend V1.7 consumiendo su API pública. No hagas deploy. No uses Vercel. La prueba se hace puramente en local con `pnpm dev`."
