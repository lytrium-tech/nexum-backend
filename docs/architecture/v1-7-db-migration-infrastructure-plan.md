> **HISTORICAL NOTICE:** Este documento conserva diseos, planes o reportes histricos. No debe ser considerado la fuente de verdad actual. Para la documentacin t?cnica cannica, consulte [docs/project/](../project/00-backend-overview.md).

# Nexum Core Obligations V1.7 — Phase 2.2 DB Migration Infrastructure Plan

## 1. Executive Summary
Antes de aplicar cualquier migración de esquema en producción para habilitar V1.7, es imperativo establecer una infraestructura segura que permita probar las migraciones estructurales y de datos en un entorno aislado. Este plan documenta la estrategia para configurar, validar y ejecutar migraciones Alembic de manera predecible, mitigando riesgos operativos sobre V1.5.

## 2. Current Migration Gap
Actualmente, el proyecto posee una deficiencia en su flujo de migraciones:
- **No hay Alembic path real:** Los scripts bajo `scripts/migration/` operan manualmente y no existe un `alembic.ini` inicializado nativamente que permita usar los comandos de CLI (`upgrade`, `downgrade`, `history`).
- **No hay DB desechable validada:** Falta un entorno local estándar o de pruebas CI/CD con un seed del esquema actual que pueda clonarse on-demand.
- **Sin validación de Upgrade/Downgrade:** El script borrador de V1.7 Phase 2.1 (`alembic_v17_phase2_draft.py`) es sintácticamente correcto, pero no ha sido ejecutado contra una base de datos para verificar fallos imprevistos o impactos en la estructura legacy.
- **Riesgo Productivo:** Producción nunca debe ser el primer entorno de prueba para una alteración estructural en esquemas críticos como los financieros.

## 3. Opciones evaluadas

### Opción A — Docker Postgres local disposable
Uso de `docker-compose` para levantar efímeramente una base PostgreSQL local, inicializarla con un volcado o seed script del esquema V1.5 y correr Alembic allí.
- **Ventajas:** Rápido, costo cero, latencia nula, control total, ideal para iteraciones locales y CI automatizado.
- **Desventajas:** No replica la red ni la infraestructura de cloud; los datos son sintéticos (o requiere anonimización si se exporta de prod).
- **Costo:** $0.
- **Complejidad:** Baja (asumiendo familiaridad con Docker).
- **Nivel de confianza:** Alto para validación estructural pura.
- **Cuándo usarla:** Durante la fase de desarrollo y para validación pre-commit de los desarrolladores.

### Opción B — Supabase staging project / branch
Crear un proyecto satélite o usar database branching en Supabase.
- **Ventajas:** Replica exactamente la versión, extensiones y configuraciones del Postgres de producción en Supabase. Posibilidad de hacer branch de los datos reales para testing de alta fidelidad.
- **Desventajas:** Dependencia de red, límites en free tier para branching, sobrecarga en la configuración de keys/secrets.
- **Costo:** Dependiente del tier (usualmente incluido en planes pro, o requiere crear proyecto nuevo gratuito con mantenimiento manual).
- **Complejidad:** Media.
- **Nivel de confianza:** Muy alto (replica la infraestructura cloud).
- **Cuándo usarla:** Para un entorno de Staging persistente o UAT (User Acceptance Testing) pre-release masivo.

### Opción C — DB staging en VPS
Desplegar otra instancia de Postgres o schema temporal dentro del actual VPS de Nexum.
- **Ventajas:** Bajo control del equipo, comparte latencia de backend si el entorno de staging se despliega allí mismo.
- **Desventajas:** Riesgo de contención de recursos en el servidor o caída cruzada si no se aísla bien, mantenimiento manual tedioso.
- **Costo:** $0 extra (usa recursos ociosos del VPS actual).
- **Complejidad:** Alta (operaciones manuales de DevOps, aislamiento de red/puertos).
- **Nivel de confianza:** Medio-Alto.
- **Cuándo usarla:** Solo si no es posible la Opción B y se desea un staging remoto persistente.

## 4. Recomendación
La estrategia óptima se dividirá en dos etapas para balancear velocidad e integridad:
- **Fase 2.2A (Desarrollo Inmediato): Opción A (Docker Postgres local disposable)**. Se habilitará inmediatamente un entorno Docker local para validar de manera veloz y reiterada el upgrade/downgrade del esquema y la integridad de los tests (Fase 2.3).
- **Fase 2.2B (Pre-Lanzamiento V1.7): Opción B o C (Staging persistente)**. Antes del rollout real de los writes a producción, se desplegará una rama (o entorno análogo) que servirá como validación final de contratos y dashboard frente al frontend.

## 5. Alembic Strategy
El flujo real de migraciones será estandarizado como sigue:
- **Ruta Oficial:** Inicializar un entorno Alembic en `alembic/` (o si se decide mantener `scripts/migration`, formalizar un `alembic.ini` que apunte a ese path).
- **`alembic.ini`:** Se incluirá en la raíz del proyecto para permitir el uso natural de `uv run alembic upgrade head`.
- **Naming Convention:** `YYYYMMDD_HHMMSS_<slug>.py` o revisiones cortas hash (dependiente de autogenerate) para mantener orden temporal estricto.
- **Revision / Down_revision:** Obligatorias. Deben formar un árbol lineal o ramificado resuelto.
- **Conexión DB:** Variables de entorno (`DATABASE_URL`) dictarán el target (Local, Staging, Producción).
- **Comandos base:**
  - *Upgrade:* `alembic upgrade head` o `alembic upgrade +1`.
  - *Downgrade:* `alembic downgrade -1` o `alembic downgrade base`.
  - *Current/History:* `alembic current`, `alembic history`.

## 6. Disposable DB Strategy
Plan para levantar el entorno efímero local (Fase 2.3):
1. **Docker Compose:** Proveer un `docker-compose.db.yml` simple (solo Postgres 15/16).
2. **Environment Variables:** Crear un `.env.test` donde `DATABASE_URL=postgresql://user:pass@localhost:5432/nexum_test`.
3. **Seed Schema (V1.5 Compatible):** Mediante un script SQL inicial (`init.sql`), replicar la estructura productiva actual de V1.5.
4. **Aplicar Migración:** Correr la herramienta Alembic contra la DB local.
5. **Inspeccionar Objetos:** Validar las nuevas tablas y columnas.
6. **Ejecutar Tests:** `uv run pytest tests/ -v` asegurando que V1.5 legacy mantenga la tasa de 232 passed.
7. **Destruir DB:** Detener y limpiar el contenedor tras el éxito (o automatizarlo en pytest fixtures/CI).

## 7. Production Safety Rules
Reglas inquebrantables antes, durante y después del deploy de migraciones en Producción:
1. Producción **nunca** es el primer entorno de migración; cualquier alteración debe haber pasado previamente por Staging o Disposable DB.
2. **Backup obligatorio** automatizado o manual justo antes de la migración productiva.
3. **Feature Flag OFF:** El feature flag lógico de V1.7 debe estar estrictamente OFF durante el despliegue para evitar inconsistencias en el periodo de ventana de la migración de DB.
4. **Validación Health/Readiness:** Comprobar que los endpoints base de V1.5 responden tras ejecutar el upgrade.
5. **Dashboard/Intelligence Smoke Test:** Obligatorio confirmar que el dashboard y Nexum AI operan con normalidad.
6. **Rollback Primario (Runtime Reversibility):** Si un bug ocurre en producción, el rollback primario es mantener el **feature flag OFF**. No usar downgrade como pánico.
7. **Downgrade Estructural Restringido:** Ejecutar un downgrade masivo en producción es altamente destructivo si V1.7 ya generó datos. Solo se permite downgrades estructurales si **no ha habido writes V1.7** o en un ambiente netamente controlado/staging.

## 8. Acceptance Criteria
La Fase 2 de base de datos solo se considerará superada y habilitará el inicio de código (Fase 3) cuando exista un plan empírico verificado de:
- Se puede ejecutar upgrade local/staging exitosamente sin errores fatales.
- Se puede ejecutar downgrade local/staging para revertir.
- Validación estructural (ver tablas, nulos, y metadatos) pasa auditoría.
- Al ejecutar las pruebas (tests), `V1.5` continúa operando intacto.
- Se redacta un reporte final con estas confirmaciones.

## 9. Next Step Proposed
**Phase 2.3 — Disposable DB Setup & Migration Validation**
Se recomienda iniciar esta subfase donde se inicializará efectivamente la base de prueba Docker, se configurará `alembic.ini`, se refactorizará el draft `alembic_v17_phase2_draft.py` en una revisión nativa conectada al árbol, y se correrá la prueba de fuego de upgrade y testing contra V1.5.

