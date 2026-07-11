# 01 — Architecture

## Modular FastAPI
El proyecto adopta una arquitectura modular basada en FastAPI. El enrutamiento de dependencias (Dependency Injection) de FastAPI se usa intensivamente para obtener sesiones de base de datos (`get_db`) y contexto de autenticación (`get_current_user`).

## Estructura de Capas
1. **Routers (`router.py`):** Definen los endpoints HTTP, inyectan dependencias, manejan las peticiones y retornan respuestas tipadas mediante esquemas Pydantic.
2. **Schemas (`schemas.py`):** Modelos Pydantic responsables de la validación de entrada (payloads) y la definición estricta de la salida (responses). No contienen lógica de negocio.
3. **Services (`service.py`):** Contienen la lógica de negocio core. Realizan validaciones funcionales cruzadas, ejecutan cálculos (ej. Smart Payment delegators, FX Engine) y operan sobre repositorios o bases de datos.
4. **Models (`models.py`):** Entidades de SQLAlchemy que definen las tablas e interacciones relacionales en la base de datos PostgreSQL.

## Financial Ledger
El corazón financiero reside en el módulo Ledger. Cada mutación transaccional se traduce en `financial_events` (doble partida o eventos inmutables). Esto significa que un pago (comando) no solo modifica el saldo del período, sino que genera un rastro inmutable que explica de dónde provino el dinero y hacia dónde fue.

## Comandos vs Lecturas (CQRS-lite)
Existe una separación conceptual:
- **Endpoints de Lectura (GET):** Rápidos, agregan información, realizan unificación (ej. `overview` y `summary`).
- **Endpoints de Comando (POST/PATCH):** Transaccionales, utilizan bloqueos donde es necesario, validan reglas de negocio estrictas, generan eventos en el Ledger, y delegan a motores de lógica (ej. `PeriodEngine`, `PaymentAllocator`).

## Integraciones Externas y Fuente de Verdad
Cualquier dependencia de un servicio externo (como el precio del dólar) se ingesta, se envuelve (wrapper) y se "congela" temporalmente en un Snapshot de la base de datos para que las validaciones de negocio posteriores se hagan contra el estado interno, asegurando inmutabilidad ante fallas de la red o cambios de tasa a mitad de un proceso.

---
*Last verified against `367ebdc`*