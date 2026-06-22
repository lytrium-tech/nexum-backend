# Backend V1.4 Production Deploy Report

## 1. Commit desplegado
- **Local commit:** 2769a7e docs: close backend v1.4 handoff
- **VPS commit:** 2769a7e docs: close backend v1.4 handoff

## 2. Ruta VPS usada
`/opt/nexum-backend`

## 3. Docker status
```text
NAME                IMAGE               COMMAND                  SERVICE   CREATED         STATUS                                     PORTS
nexum_backend_api   nexum-backend-api   ".venv/bin/uvicorn a…"   api       Up (healthy)    127.0.0.1:8010->8000/tcp
```

## 4. Health / Readiness
- **Health:** `{"status":"ok","service":"nexum-backend"}`
- **Readiness:** `{"status":"ok","service":"nexum-backend"}`

## 5. OpenAPI Production Check
- El endpoint `https://api.nexum.lytrium.tech/openapi.json` retorna `404 Not Found`.
- Production OpenAPI endpoint is disabled by config (`DEBUG=False` en `app/main.py`).
- Frontend must use shared OpenAPI at `../docs/contracts/openapi.json`.
- El `openapi.json` local sí fue verificado y contiene `estimated_totals` y las reglas de `currency` mandatorias.

## 6. Migraciones ejecutadas
- **No.** Backend V1.4 no incluyó cambios de esquema de base de datos que requirieran nuevas migraciones.

## 7. Issues found
- El `openapi.json` de producción da 404 por `DEBUG=False`. Si el frontend necesita acceder al contrato remoto en producción, nginx tendría que servir el archivo estático, o habría que habilitarlo mediante otra variable. Por ahora, el contrato se distribuye vía repo (`openapi.json`).

## 8. Frontend retest required
- **Sí.** Es necesario verificar en Frontend V1.4 (o V1.4.1) que la creación de transacciones en USD funciona ahora que la API de producción (`api.nexum.lytrium.tech`) efectivamente ejecuta el código V1.4.
