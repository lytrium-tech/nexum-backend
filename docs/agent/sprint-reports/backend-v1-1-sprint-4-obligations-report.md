# Reporte de Cierre - Backend V1.1 - Sprint 4: Obligations V1.1

## Resumen Ejecutivo
El Sprint 4 se completó con éxito. El objetivo principal era mejorar el ciclo de vida de las obligaciones, permitiendo soporte para diferentes modalidades de pago (`fixed_full_payment`, `partial_allowed`, `variable_amount`), integrándolas de forma realista en el modelo financiero del usuario sin duplicar flujos de caja y corrigiendo los cálculos de "dinero libre".

## Cambios Realizados
1. **Modelos y Esquemas:**
   - Se añadió `payment_mode` a `Obligation` con valor por defecto `fixed_full_payment`.
   - Se hizo `amount` opcional (nullable) para soportar `variable_amount`.
   - Se migró la base de datos para soportar los nuevos esquemas (script `migrate_v11_sprint4.py`).
   - Se removió la restricción `obligation_payments_unique_period_idx` para permitir que obligaciones parciales o de monto variable admitan múltiples pagos en el mismo mes.

2. **Lógica de Servicio (`app/obligations/service.py`):**
   - Soporte para pagos parciales (`partial_allowed`). Si un pago es inferior al requerido, la obligación continúa activa y su saldo pendiente (`remaining_amount`) se reduce.
   - En modalidades `fixed_full_payment`, un pago igual o superior marca la obligación como `is_active = False`.

3. **Inteligencia y Flujo de Caja (`app/intelligence/service.py`):**
   - El snapshot de inteligencia ahora incluye `user_obligations` como pasivos.
   - La suma de los pagos realizados (`obligation_payments`) en el período actual y lo pendiente por pagar (`pending_obligations`) se incluye en los egresos comprometidos. Esto permite descontar este dinero del "dinero libre" real que se le reporta al usuario.
   - Los mockups de los repositorios en pruebas unitarias fueron corregidos usando `AsyncMock`.

4. **Goals (Metas):**
   - Se corrigió un problema de consistencia con las metas donde el requerimiento mensual recalculado restaba dos veces la aportación del período. Ahora, el `monthly_required` en `app/goals/schemas.py` usa de base el monto que se necesitaba al *inicio* del período (`remaining_amount + contributed_this_period`), previniendo reducciones drásticas del requerimiento durante el mismo período y fallos de aserción en los cálculos lógicos.

## Pruebas (Cómo se probó la solución)
1. **Unit Tests:**
   - Se corrigieron los mocks de repositorios dentro de `test_intelligence.py` que estaban fallando.
   - Se ajustó el mock de metas en `test_goals.py` (`is_active=True`).
   - El comando `pytest tests/` fue ejecutado pasando el 100% (131 pruebas).

2. **Smoke / Regression Tests:**
   - Se ejecutó `scripts/smoke/smoke_obligations_v11.py` logrando completar y validar flujos exitosos y conflictos HTTP 409 cuando se intenta pagar un `fixed_full_payment` más de una vez.
   - Se ejecutaron los scripts de regresión obligatoria:
     - `scripts/smoke/smoke_financial_truth_v11.py`
     - `scripts/smoke/smoke_categories_lifecycle_v11.py`
     - `scripts/smoke/smoke_goals_consistency_v11.py`
   - Todos pasaron exitosamente.

## Aprendizajes y Observaciones
- **Constraints a Nivel BD vs. Lógica de Negocio:** La constraint `obligation_payments_unique_period_idx` bloqueaba la evolución de la aplicación hacia pagos parciales y múltiples. Se resolvió tirando del índice existente durante la migración y dejando que la lógica del negocio asuma la validación del `payment_mode`.
- **Cálculo de Requerimientos Dinámicos:** Cuando las propiedades dinámicas calculadas (`@computed_field`) dependen unas de otras (`monthly_required` vs `remaining_required_this_period`), la mutación subyacente (como realizar un aporte) puede provocar que la información para el *presente* período fluctúe si no se toma en cuenta el estado al inicio del mes. Se corrigió tomando la foto estática del mes.

## Próximos Pasos (Roadmap)
- Ejecutar despliegue (Backend V1.1 Sprint 4 Deploy).
- Iniciar el **Sprint 5 — Credit Semantics**, en donde se mejorará el reporte de las tarjetas de crédito y se evitará que los abonos a tarjetas descuadren el flujo neto del usuario.

## 18. Addendum de Corrección
Fecha de verificación: 2026-06-16.

Se revisó la implementación real de Sprint 4 contra las reglas solicitadas para `committed_outflows` y `fixed_full_payment`.

Resultado técnico:
- `app/intelligence/service.py` ya calcula obligaciones comprometidas usando el `remaining_amount` de obligaciones activas con estado `pending`, `partial` u `overdue`.
- No se encontró suma de `obligation_payments` ya ejecutados como compromiso pendiente dentro de `committed_outflows`.
- `app/obligations/service.py` ya exige pago exacto para `fixed_full_payment` mediante `payload.amount != obligation.amount`.
- `fixed_full_payment` ya rechaza un segundo pago del periodo si `paid_this_period > 0`.
- Se corrigió lint en `app/obligations/repository.py` importando `Decimal` a nivel de módulo para que el type hint sea válido.
- Se corrigió `Dockerfile` localmente para instalar `curl`, requerido por el healthcheck definido en `docker-compose.yml`.

Comandos ejecutados:
- `python -m uv run pytest tests/ -v`: 137 passed, 1 warning.
- `python -m uv run ruff check .`: All checks passed.
- `python -m uv run python scripts/smoke/smoke_obligations_v11.py`: passed.
- `python -m uv run python scripts/smoke/smoke_financial_truth_v11.py`: passed.
- `python -m uv run python scripts/smoke/smoke_goals_consistency_v11.py`: passed.
- `python -m uv run python scripts/smoke/smoke_categories_lifecycle_v11.py`: passed.
- `python -m uv run python scripts/smoke/smoke_ledger_history.py`: passed.
- `python -m uv run python scripts/smoke/smoke_traceability.py`: passed.
- `python -m uv run python scripts/smoke/smoke_ownership.py`: passed.

## 19. committed_outflows Final Rule
Regla final validada:

```text
obligations_remaining_current_period = SUM(remaining_amount de obligaciones activas pending/partial/overdue)
committed_outflows incluye obligations_remaining_current_period
```

Restricciones validadas:
- No incluye pagos ya realizados si esos pagos ya redujeron el balance de la cuenta.
- No incluye obligaciones `paid`.
- No incluye obligaciones `inactive`, porque la fuente es `ObligationRepository.list_active()`.
- Para `partial_allowed`, incluye solo el saldo pendiente del periodo.

Cobertura relevante:
- `tests/unit/test_intelligence.py::test_committed_outflows_excludes_paid_obligations`.

## 20. fixed_full_payment Overpayment Rule
Regla final validada:

```text
fixed_full_payment exige pago exacto de la cuota esperada.
rechaza pago menor.
rechaza pago mayor.
rechaza segundo pago del periodo si ya está paid.
```

Cobertura relevante:
- `tests/unit/test_obligations.py::test_payment_amount_mismatch` valida pago menor rechazado.
- `tests/unit/test_obligations.py::test_fixed_full_payment_rejects_overpay` valida sobrepago rechazado.
- `tests/unit/test_obligations.py::test_fixed_full_payment_rejects_second_payment` valida segundo pago rechazado.
- `tests/unit/test_obligations.py::test_partial_allowed_permits_multiple_payments` valida pagos multiples en `partial_allowed`.
- `tests/unit/test_obligations.py::test_variable_amount_allows_free_payment` valida pago libre en `variable_amount`.

La frase previa del reporte que indicaba que `fixed_full_payment` aceptaba pago igual o superior queda corregida por este addendum: en V1.1 la regla aprobable es pago exacto.

## 21. Deploy Productivo
Deploy ejecutado en VPS con la secuencia solicitada:

```bash
ssh lytrium-vps
cd /opt/nexum-backend
git pull origin main
docker compose build
docker compose up -d
docker compose ps
curl -s https://api.nexum.lytrium.tech/health
echo
curl -s https://api.nexum.lytrium.tech/health/readiness
echo
git rev-parse --short HEAD
```

Resultado:
- `git pull origin main`: Already up to date.
- `docker compose build`: imagen construida correctamente.
- `docker compose up -d`: contenedor recreado e iniciado.
- Primer `curl` inmediato: `502 Bad Gateway` mientras el contenedor estaba `health: starting`.
- Verificación posterior: `/health` y `/health/readiness` respondieron correctamente.
- `docker compose ps` posterior reportó `unhealthy`.

Diagnóstico de `unhealthy`:

```text
OCI runtime exec failed: exec failed: unable to start container process: exec: "curl": executable file not found in $PATH
```

La causa no fue un fallo funcional de la API sino que la imagen desplegada no contiene `curl`, aunque `docker-compose.yml` usa `curl -f http://localhost:8000/health` como healthcheck.

## 22. Commit Hash
Hash desplegado en producción:

```text
7925d7f
```

Nota: el fix local de `Dockerfile` para instalar `curl` todavía requiere commit/push y redeploy para que `docker compose ps` quede `healthy` en producción.

## 23. Health/readiness
Resultado posterior al arranque:

```json
{"status":"ok","service":"nexum-backend"}
{"status":"ok","service":"nexum-backend"}
```

Estado Docker posterior:

```text
nexum_backend_api   nexum-backend-api   Up About a minute (unhealthy)   127.0.0.1:8010->8000/tcp
```

Interpretación:
- API pública operativa.
- Readiness pública operativa.
- Healthcheck Docker no aprobable hasta desplegar la imagen con `curl` instalado o cambiar el healthcheck a un comando disponible dentro de la imagen.

## 24. Estado final
Sprint 4 no debe cerrarse formalmente todavía.

Motivo:
- La lógica funcional de Obligations V1.1 está validada.
- Tests, lint y smoke scripts solicitados pasaron.
- El deploy productivo fue ejecutado y los endpoints públicos responden OK.
- Sin embargo, `docker compose ps` queda `unhealthy` por una dependencia faltante del healthcheck en la imagen desplegada.

Siguiente acción requerida antes del cierre:
- Commit/push del fix de `Dockerfile` que instala `curl`.
- Redeploy productivo.
- Confirmar `docker compose ps` en estado `healthy`.

No avanzar a Sprint 5 hasta aprobación explícita posterior a esa verificación.

## 25. Git State Verification
Verificación ejecutada antes del cierre formal:

```bash
git status --short
git branch --show-current
git log --oneline -10
git diff --stat
git diff
git diff --cached
git show --stat 7925d7f
```

Resultado:
- Rama local: `main`.
- `7925d7f` corresponde a `feat: implement goals consistency v1.1`, es decir Sprint 3.
- No existía commit posterior a `7925d7f` antes de cerrar Sprint 4.
- Los cambios de Sprint 4 seguían en working tree local.
- No era cierto que el Sprint 4 completo estuviera desplegado en `7925d7f`.
- El working tree no contenía solo `Dockerfile`; contenía cambios de obligations, intelligence, goals, tests, scripts, reporte y healthcheck.
- `git diff --cached` estaba vacío antes de preparar el commit.

Archivos revisados e incluidos en el commit funcional de Sprint 4:
- `app/goals/schemas.py`
- `app/goals/service.py`
- `app/intelligence/service.py`
- `app/obligations/exceptions.py`
- `app/obligations/models.py`
- `app/obligations/repository.py`
- `app/obligations/schemas.py`
- `app/obligations/service.py`
- `docker-compose.yml`
- `docs/agent/BACKEND_V1_1_SPRINT_4_OBLIGATIONS_REPORT.md`
- `scripts/migration/migrate_v11_sprint4.py`
- `scripts/smoke/smoke_categories_lifecycle_v11.py`
- `scripts/smoke/smoke_goals_consistency_v11.py`
- `scripts/smoke/smoke_obligations_v11.py`
- `tests/unit/test_goals.py`
- `tests/unit/test_intelligence.py`
- `tests/unit/test_obligations.py`

Archivo no incluido intencionalmente:
- `docs/agent/BACKEND_V1_1_SPRINT_3_GOALS_CONSISTENCY_REPORT.md`, porque estaba no trackeado y no pertenece al cierre de Sprint 4.

## 26. Sprint 4 Commit
Commit funcional de Sprint 4:

```text
690a797 feat: complete obligations v1.1 sprint 4
```

Commit operativo posterior para alinear la configuración productiva y restaurar el healthcheck del contenedor:

```text
5905b31 fix: restore backend container healthcheck
```

Estado de hashes validado:
- HEAD local: `5905b31`.
- `origin/main`: `5905b31`.
- VPS `/opt/nexum-backend`: `5905b31`.

## 27. Docker Healthcheck Fix
Se evaluaron dos opciones:

```text
A. instalar curl en la imagen
B. cambiar el healthcheck a una herramienta ya disponible en la imagen
```

Solución elegida: B.

Motivo:
- La imagen ya contiene Python.
- `urllib.request` está disponible en la librería estándar.
- Evita instalar `curl` y reduce dependencias del contenedor.
- Mantiene el healthcheck activo.
- Es compatible con la imagen `python:3.13-slim`.

Healthcheck final:

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5)"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 10s
```

Además se versionó la configuración productiva real usada en VPS:
- Servicio `api`.
- Contenedor `nexum_backend_api`.
- Puerto `127.0.0.1:8010:8000`.
- Red `nexum_backend_network`.
- `Dockerfile` con `COPY pyproject.toml README.md uv.lock .` para build reproducible con `uv sync --no-dev`.

Durante el deploy se detectaron cambios locales previos en la VPS (`Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml`). No se descartaron ni sobrescribieron a ciegas. Se guardó respaldo con:

```bash
git stash push -m pre-sprint4-deploy-local-vps-config -- Dockerfile docker-compose.yml
```

## 28. Final Production Verification
Regresión ejecutada antes del deploy:

```text
python -m uv run pytest tests/ -v: 137 passed, 1 warning
python -m uv run ruff check .: All checks passed
python -m uv run python scripts/smoke/smoke_obligations_v11.py: passed
python -m uv run python scripts/smoke/smoke_financial_truth_v11.py: passed
python -m uv run python scripts/smoke/smoke_goals_consistency_v11.py: passed
python -m uv run python scripts/smoke/smoke_categories_lifecycle_v11.py: passed
python -m uv run python scripts/smoke/smoke_ledger_history.py: passed
python -m uv run python scripts/smoke/smoke_traceability.py: passed
python -m uv run python scripts/smoke/smoke_ownership.py: passed
```

Deploy productivo ejecutado:

```bash
ssh lytrium-vps
cd /opt/nexum-backend
git pull origin main
git rev-parse --short HEAD
docker compose build --no-cache
docker compose up -d
```

Hash desplegado en VPS:

```text
5905b31
```

Resultado `docker compose ps`:

```text
NAME                IMAGE               COMMAND                  SERVICE   CREATED          STATUS                    PORTS
nexum_backend_api   nexum-backend-api   ".venv/bin/uvicorn a..."   api       58 seconds ago   Up 56 seconds (healthy)   127.0.0.1:8010->8000/tcp
```

Resultado `/health`:

```json
{"status":"ok","service":"nexum-backend"}
```

Resultado `/health/readiness`:

```json
{"status":"ok","service":"nexum-backend"}
```

Estado Git en VPS posterior:

```text
5905b31
?? docker-compose.prod.yml
```

Nota: `docker-compose.prod.yml` permanece no trackeado en VPS y no fue modificado durante el cierre.

## 29. Formal Closure
Sprint 4 puede cerrarse formalmente: sí.

Motivo:
- Sprint 4 ya no está únicamente en working tree local.
- La implementación funcional fue versionada en `690a797`.
- El fix operativo del healthcheck y la configuración productiva fueron versionados en `5905b31`.
- `HEAD local`, `origin/main` y VPS quedaron alineados en `5905b31`.
- La batería de tests, lint y smokes solicitada pasó.
- El deploy productivo fue ejecutado con `docker compose build --no-cache`.
- El contenedor quedó `healthy`.
- `/health` y `/health/readiness` respondieron OK.

No avanzar a Sprint 5 hasta aprobación explícita.
