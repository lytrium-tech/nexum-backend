# 03 — API Contracts and OpenAPI

## OpenAPI como Fuente de Verdad HTTP
El contrato público del backend de Nexum está gobernado estrictamente por el archivo `openapi.json` que reside en la raíz del repositorio. Cualquier cambio en las firmas, rutas o payloads debe estar respaldado por una regeneración y commit de este archivo.

## Versiones de Rutas Expuestas
Actualmente el backend expone funcionalidades bajo dos prefijos de versión principales:
- `/api/v1/`: Rutas legacy y módulos consolidados tempranos (ej. Auth base, Accounts, Credit Cards V1.5).
- `/api/v1.7/`: El core operativo actual para el manejo de pasivos (Obligations) y transacciones cruzadas de divisas (FX).

### Aclaración sobre "V1.8"
En el registro de desarrollo (commits y PRs), es común encontrar referencias a la etiqueta funcional "V1.8". **V1.8 no es una API independiente**, sino una etiqueta evolutiva de capacidades (como el Smart Payment, el endpoint de Overview, o la idempotencia de FIFO) construidas **sobre las mismas rutas `/api/v1.7/`**. No existe (ni debe asumirse la existencia de) un prefijo de ruta `/api/v1.8/` en el código base.

## Características de la API
- **Autenticación:** Vía Headers (`Authorization: Bearer <token>`).
- **Idempotencia:** Endpoints críticos de comando, como los pagos cruzados, soportan la cabecera `Idempotency-Key` (o un campo equivalente en payload) para evitar débitos múltiples accidentales.
- **Respuestas Tipadas:** Errores estructurados (400, 401, 403, 404, 500) según las definiciones de FastAPI.

## Contratos Legacy vs Actuales
En el módulo Obligations V1.7, el contrato actual requiere del uso de un `rate_snapshot_id` para garantizar las tasas de cambio de FX. Sin embargo, para mantener compatibilidad temporal con versiones antiguas del frontend, campos como `source_amount` y `quote_id` aún figuran como **opcionales** dentro de los esquemas en `openapi.json`. Deben tratarse como deuda técnica de interfaz.

## Proceso Oficial de Regeneración de OpenAPI
Cuando un agente o desarrollador altera un router o un schema Pydantic, el proceso oficial requiere:
1. Validar que la API levanta (`pytest` local).
2. Ejecutar el script (ej. `python scripts/dev/generate_openapi.py` o método nativo acordado) para sobrescribir `openapi.json`.
3. Revisar el diff y versionar (commit) el archivo junto a la lógica de código.

---
*Last verified against `367ebdc`*