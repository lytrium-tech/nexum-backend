# Nexum V1.5 — Pay Early & Preview FX Hotfix Deploy Report

## Resumen de Despliegue
Se ha desplegado a producción (`https://api.nexum.lytrium.tech`) el hotfix backend para solucionar errores en pago anticipado y agregar endpoints de preview FX.

## Identificadores de Commit
- **Local commit:** `bed5c8e`
- **VPS commit:** `bed5c8e`

## Estado de Producción
- **Migraciones ejecutadas:** no
- **Docker status:** healthy
- **Health:** ok
- **Readiness:** ok

## Validación (Runtime Smoke)
Se verificó el despliegue mediante comprobaciones de salud del contenedor, sin acceso a token QA seguro para pruebas end-to-end completas.
- commit VPS coincidente
- docker healthy
- health/readiness respondiendo HTTP 200 OK

## Archivos modificados en el commit desplegado
- `app/credit/router.py`
- `app/credit/schemas.py`
- `app/credit/service.py`
- `app/obligations/router.py`
- `app/obligations/schemas.py`
- `app/obligations/service.py`
- `openapi.json`
- `docs/agent/reports/nexum-v1-5-pay-early-500-and-fx-preview.md`

## Notas y Limitaciones
- Compatibilidad frontend: El contrato OpenAPI se ha actualizado y expandido pasivamente. Los endpoints de preview están disponibles para que el Frontend los consuma. No hay breaking changes reportados.
- Se requiere retest de QA en Runtime para probar los pagos anticipados elegibles con montos cruzados.

## Siguientes Pasos Recomendados
El agente o equipo Frontend puede proceder a integrar `POST .../preview` para el renderizado del Payment FX.
