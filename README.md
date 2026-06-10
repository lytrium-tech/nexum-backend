# Nexum Backend

Backend propio de **Nexum**, sistema operativo financiero personal impulsado por IA, construido por [Lytrium](https://lytrium.co).

## Propósito

Este backend reemplazará progresivamente la lógica actualmente implementada en n8n. Está diseñado como un **monolito modular** usando FastAPI, SQLAlchemy asíncrono y PostgreSQL (Supabase).

> **Importante:** n8n es considerado legado temporal y referencia histórica únicamente. No define la arquitectura de este backend.

## Estado actual

- Fase 2: Scaffold inicial creado.
- No se han ejecutado migraciones contra Supabase.
- No se han realizado cambios estructurales en la base de datos.
- Docker existe solo como archivo de repositorio y **no debe ejecutarse localmente** en esta fase.

## Estructura

```text
backend/
├── app/
│   ├── main.py               # Entrada principal de FastAPI
│   ├── core/                 # Módulos transversales
│   │   ├── config.py         # Configuración con pydantic-settings
│   │   ├── database.py       # SQLAlchemy async engine y sesiones
│   │   ├── errors.py         # Excepciones base y handlers
│   │   ├── logging.py        # Configuración structlog
│   │   ├── security.py       # Placeholder de autenticación
│   │   └── idempotency.py    # Placeholder de idempotencia
│   ├── users/                # Dominio: usuarios
│   ├── accounts/             # Dominio: cuentas
│   ├── categories/           # Dominio: categorías
│   ├── ledger/               # Dominio: ledger financiero (financial_events)
│   ├── cash/                 # Dominio: ingresos y gastos
│   ├── goals/                # Dominio: metas de ahorro
│   ├── obligations/          # Dominio: obligaciones
│   ├── credit/               # Dominio: tarjetas de crédito (bloqueado hasta validación)
│   ├── intelligence/         # Dominio: inteligencia financiera
│   ├── conversations/        # Dominio: estado conversacional y pending actions
│   ├── integrations/         # Dominio: integraciones externas (Gemini)
│   └── api/
│       └── router.py         # Agregador de rutas v1
├── tests/                    # Pruebas
├── migrations/               # Alembic (pendiente de inicialización)
├── scripts/                  # Scripts de utilidad
├── docs/                     # Documentación técnica del backend
├── .env.example              # Plantilla de variables de entorno
├── pyproject.toml            # Dependencias y configuración
├── Dockerfile                # Solo para repositorio — no ejecutar localmente
├── docker-compose.yml        # Solo para repositorio — no ejecutar localmente
└── README.md
```

## Instalación con uv

Asegúrate de tener [uv](https://docs.astral.sh/uv/) instalado:

```bash
# Instalar uv (si no lo tienes)
pip install uv

# Crear entorno virtual e instalar dependencias
uv sync

# Para instalar dependencias de desarrollo también
uv sync --extra dev
```

## Ejecución local (sin Docker)

```bash
# Copiar y configurar variables de entorno
cp .env.example .env
# Editar .env con tus valores reales

# Ejecutar servidor de desarrollo
uv run uvicorn app.main:app --reload --port 8000
```

El servidor estará disponible en: `http://localhost:8000`

Health check: `http://localhost:8000/health`

Documentación automática: `http://localhost:8000/docs`

## Ejecución de pruebas

```bash
uv run pytest
```

## Docker

Los archivos `Dockerfile` y `docker-compose.yml` existen únicamente como parte del repositorio.

**No ejecutar Docker localmente en esta fase.**

La validación con Docker se realizará en la VPS, previa confirmación explícita de acceso.

## Stack técnico

| Herramienta | Propósito |
|---|---|
| Python 3.13+ | Lenguaje principal |
| FastAPI | Framework web |
| SQLAlchemy (async) | ORM y acceso a base de datos |
| Alembic | Migraciones de base de datos |
| asyncpg | Driver PostgreSQL asíncrono |
| Pydantic v2 | Validación y configuración |
| structlog | Logging estructurado |
| httpx | Cliente HTTP asíncrono |
| pytest | Testing |
| uv | Gestor de dependencias |

## Notas sobre n8n

n8n actúa como **sistema legado temporal**. Su arquitectura, workflows y prompts no se replican en este backend. Solo se utilizan como referencia histórica para comprender el comportamiento actual del sistema.

## Pruebas de Desarrollo E2E (Fase 6.5)

Para probar el flujo transaccional de Cash contra Supabase localmente sin JWT:

1. Crea o actualiza tu archivo `.env` manualmente:
   ```ini
   APP_ENV=development
   AUTH_BYPASS_ENABLED=true
   DEV_USER_ID=tu_uuid_aqui
   DEV_USER_EMAIL=tu_email_aqui
   DATABASE_URL=postgresql+asyncpg://postgres.[ID]:[PASSWORD]@[HOST]:5432/postgres
   ```
   *Nota: Se recomienda usar el puerto directo (5432) en desarrollo para evitar conflictos asíncronos con el pooler transaccional, a menos que Supabase requiera IPv4/6543.*

2. Verifica tu conexión de base de datos de manera segura:
   ```bash
   python -m uv run python scripts/check_db.py
   ```

3. En una terminal nueva, levanta el servidor local:
   ```bash
   python -m uv run uvicorn app.main:app --reload
   ```

4. En la primera terminal, ejecuta el script de semilla (crea tu usuario dev):
   ```bash
   python -m uv run python scripts/seed_dev.py
   ```

5. Lanza la simulación completa del Cash Domain (crea cuenta, transacciona, falla a propósito y valida idempotencia):
   ```bash
   python -m uv run python scripts/smoke_cash.py
   ```

6. Una vez probados todos los flujos, limpia tu base de datos si lo deseas (borra transacciones y cuentas pero deja tu dev_user):
   ```bash
   python -m uv run python scripts/cleanup_dev.py
   ```
