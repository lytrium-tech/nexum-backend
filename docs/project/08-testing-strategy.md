# 08 — Testing Strategy

## Framework
El backend utiliza **Pytest** como motor principal de validación, complementado por herramientas nativas como `httpx` (para peticiones asíncronas) y migraciones de entorno controlado para bases de datos de test (`sqlite` en memoria o particiones de pruebas dedicadas).

## Niveles de Pruebas

### 1. Unit y Logic Tests
- **Obligations Lifecycle:** Validan la transición de estados de períodos (pending, overdue).
- **FIFO y Smart Payment:** Cobertura exhaustiva matemática y comportamental que garantiza que las distribuciones parciales no excedan el monto total de la deuda y que respeten el orden cronológico.
- **FX Engine:** Validaciones de snapshots, TTL de vencimiento, cálculos bidireccionales con manejo de precisiones flotantes usando `Decimal`.

### 2. API Contract Tests
- Garantizan que las cargas (payloads) coincidan con los esquemas Pydantic y el `openapi.json`.
- Validan que las operaciones devuelvan 400, 401, 403 según las reglas de negocio y Feature Flags.

### 3. Smoke Tests (Operacionales)
Almacenados en `scripts/smoke/`, no forman parte de la suite de `pytest`. Se usan post-deploy para validar entornos vivos y de integración con bases de datos reales.
- Authenticated vs Unauthenticated access.
- Revisión de índices y llaves foráneas (`scripts/db/`).

## Estado Validado
El estado de salud actual del código en la rama principal refleja una validación total, sin flujos críticos fallidos.
- **Última foto (Baseline de Test en 367ebdc):** 293 passed, 3 skipped, 0 failed.

*(Este valor documenta el momento en que se redactó esta versión canónica y representa el estándar mínimo de calidad que deben mantener los futuros commits).*

---
*Last verified against `367ebdc`*
