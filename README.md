# Nexum Backend

Backend del sistema operativo financiero personal **Nexum**.
Construido por [Lytrium](https://lytrium.co) utilizando **FastAPI**, **SQLAlchemy asíncrono** y **PostgreSQL (Supabase)**.

## Documentación Canónica

La documentación técnica exhaustiva de este proyecto reside en:
- `docs/project/00-backend-overview.md`
- `docs/project/01-architecture.md`
- *Consulte el resto de la serie en `docs/project/` para dominios, despliegue y contratos.*

## Estado Actual
El backend se encuentra desplegado y operativo en producción, manejando dominios core como:
- Accounts & Ledger
- Obligations (Pasivos y motor de períodos V1.7)
- Credit Cards (V1.5)
- FX Engine & Rate Snapshots

## Instalación Local

Asegúrate de tener [uv](https://docs.astral.sh/uv/) instalado.

```bash
# Sincronizar dependencias
uv sync
```

## Ejecución Local

```bash
# Configurar entorno
cp .env.example .env
# Configurar base de datos y llaves en .env

# Levantar servidor
uv run uvicorn app.main:app --reload --port 8000
```
Health check: `http://localhost:8000/health`
OpenAPI UI: `http://localhost:8000/docs`

## Pruebas

```bash
uv run pytest
```

## Despliegue
El proyecto incluye configuración de Docker `Dockerfile` y `docker-compose.yml` para despliegue en VPS (Ver `docs/project/09-deployment-and-operations.md`).
