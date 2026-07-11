# 09 — Deployment and Operations

## Entorno de Producción
El entorno de producción reside en un VPS (Virtual Private Server). La infraestructura es orquestada localmente mediante Docker Compose.
- **Ruta conocida:** `/opt/nexum-backend` (asumida y verificada históricamente, pero siempre sujeta a auditoría).
- **Servicio principal:** `api`

## Proceso de Deploy
El proceso documentado de despliegue consiste en:
1. Conexión SSH al VPS y navegación a `/opt/nexum-backend`.
2. Actualización del código (ej. `git pull origin main`).
3. **Backup Obligatorio:** Ejecución de scripts de respaldo de la base de datos (ej. `sh run_backup_cmd.sh` o herramientas manuales) **antes** de cualquier migración.
4. **Migraciones:** Ejecución controlada de `alembic upgrade head`.
5. **Reconstrucción (Build/Recreate):** Reconstruir la imagen de Docker y levantarla (`docker-compose up -d --build api`).
6. **Health Check:** Validación de disponibilidad en los endpoints públicos de salud (`/health` o `/api/v1/...`).
7. **Smoke Tests:** Validación en entorno productivo autenticado o no autenticado para asegurar integridad (ej. mediante `scripts/smoke/`).

## Rollback
En caso de fallo crítico, se debe revertir el código fuente a un commit estable anterior, reconstruir la imagen Docker y, de ser estrictamente necesario, restaurar el backup pre-migración de la base de datos si la migración no es reversible.

---
*Last verified against `367ebdc`*
