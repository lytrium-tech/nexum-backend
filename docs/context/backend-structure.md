# Backend Structure

El backend de Nexum está organizado siguiendo un patrón modular basado en FastAPI. Cada dominio funcional cuenta con su propia carpeta dentro de `app/`, conteniendo rutas, esquemas, servicios y modelos.

## Rutas Principales (Código)
*   **`app/accounts/`**: Lógica de cuentas de usuario, balances y validación de fuentes de fondos.
*   **`app/credit/`**: (V1.5) Manejo de tarjetas de crédito, transacciones, statements y motores de cálculo crediticio.
*   **`app/fx/`**: Motor de divisas. Incluye consultas a DólarAPI, generación, validación y persistencia de FX Rate Snapshots, cálculos bidireccionales y lógica de expiración de tasas.
*   **`app/goals/`**: Gestión de metas de ahorro y apartados.
*   **`app/intelligence/`**: Agregación de contexto financiero, recolección de métricas y preparación de datos para agentes de IA.
*   **`app/ledger/`**: Motor de doble partida (Financial Events) que garantiza la trazabilidad e inmutabilidad de los movimientos financieros.
*   **`app/obligations/`**: (V1.7/V1.8 funcional) Core de obligaciones. Contiene la gestión de períodos (lifecycle, auto-refresh), endpoints de pagos (FIFO, Smart Payment), overview de deudas y resúmenes.
*   **`app/users/`**: Identidad de usuario e integración base con el sistema de autenticación.
*   **`app/api/`**: Routers globales y configuración base de FastAPI.

## Convenciones Internas por Módulo
Dentro de cada módulo en `app/`, los archivos típicamente se dividen en:
*   `router.py` (o `router_v17.py`): Puntos de entrada HTTP.
*   `schemas.py` (o `schemas_v17.py`): Modelos Pydantic para validación de entrada/salida.
*   `service.py` (o `service_v17.py`): Lógica de negocio (Backend Calculates).
*   `models.py`: Declaración del ORM (SQLAlchemy).

## Infraestructura y Operaciones
*   **`alembic/`**: Configuración e historial de migraciones de base de datos.
*   **`scripts/`**: Utilidades operativas.
    *   `scripts/db/`: Inspección de base de datos y validaciones.
    *   `scripts/dev/`: Scripts para entorno de desarrollo local.
    *   `scripts/migration/`: Helpers para generación de migraciones.
    *   `scripts/smoke/`: End-to-end smoke tests (validación en entornos vivos).
*   **`tests/`**: Suite de validación automatizada (`pytest`).
    *   `tests/unit/`: Tests unitarios y de integración local (mockeando DB externa o usando SQLite en memoria/Test DB).
*   **`openapi.json`**: El contrato HTTP público oficial (estático) generado a partir de FastAPI.

## Documentación
*   **`docs/context/`**: Contexto vivo y estado actual operativo (para mantenedores y agentes).
*   **`docs/project/`**: Documentación técnica canónica y profunda sobre la arquitectura y contratos.
*   **`docs/architecture/`**: Diagramas, decisiones de diseño (ADRs) y diseños históricos.
*   **`docs/handoff/backend/`**: Contratos específicos destinados a consumidores del API (como Frontend).
*   **`docs/agent/reports/`**: Evidencia histórica generada por ejecuciones de agentes (no canónica).

---
*Last verified against `367ebdc`*
