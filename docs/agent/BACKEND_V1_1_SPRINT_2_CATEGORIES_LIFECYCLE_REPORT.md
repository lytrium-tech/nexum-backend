# Backend V1.1 - Sprint 2 Categories Lifecycle Report

## 1. Limpieza de Duplicados
Durante la fase de descubrimiento para asegurar la unicidad de las categorías por `user_id` + `type` + `normalized_name`, se detectaron categorías duplicadas previamente existentes. 

### Caso Detectado
*   **Usuario (ID):** `2b3c206f-3f83-401b-a295-c970fa4b4ae1`
*   **Tipo:** `expense`
*   **Nombre Normalizado:** `transporte`

Duplicados identificados:
1. `db15e2e5-4544-472b-b75d-221f5ae86c0e` | inactive | eventos: 0
2. `8dc20376-e00d-4440-9c1b-547923f15a65` | inactive | eventos: 0

### Acción Tomada
Bajo aprobación explícita, se ejecutó una limpieza segura:
- Se eliminó únicamente la fila `8dc20376-e00d-4440-9c1b-547923f15a65`.
- Se conservó la fila más antigua (`db15e2e5-4544-472b-b75d-221f5ae86c0e`).
- Se verificó que `financial_events` no fuera afectado (eventos asociados = 0).
- Se ejecutó nuevamente la validación confirmando **0 duplicados** restantes en la base de datos.

---

## 2. Implementación de Restricciones (Database Constraints)
Una vez limpia la base de datos, se creó y aplicó la migración `scripts/migrate_v11_sprint2.py`:
- Se agregó la columna `normalized_name` a la tabla `categories`.
- Se aplicó la normalización a todas las filas existentes en base de datos.
- Se agregó un `UniqueConstraint` en `(user_id, type, normalized_name)` bajo el nombre `uq_category_user_type_normalized_name`. Esto garantiza unicidad incluso en categorías inactivas a nivel de esquema.

---

## 3. Actualización de API e Implementación Lógica
- `GET /api/v1/categories`: Se agregó el query param `include_inactive: bool = False` para permitir la recuperación de categorías inactivas.
- `PATCH /api/v1/categories/{category_id}`: Se amplió la lógica para soportar el cambio de estado con el payload `{"is_active": true/false}`. Adicionalmente, el intento de renombrar una categoría valida contra las activas e inactivas del mismo tipo.
- **Categorías Globales / Fallback `sin_clasificar`**: Se confirmó que `sin_clasificar` se encuentra presente con `user_id IS NULL`, es inmutable e indeleble por diseño, y su acceso está protegido mediante las lógicas de ownership en `CategoryService`.

---

## 4. Pruebas y Validación (Smoke Tests)
Se creó el script de regresión `scripts/smoke_categories_lifecycle_v11.py` el cual cubre el ciclo completo:
1. Validar que `sin_clasificar` existe y es global.
2. Intentar editar la categoría global (falla con 403 Forbidden).
3. Crear una categoría privada (Transporte).
4. Listar las categorías activas.
5. Desactivarla usando PATCH.
6. Confirmar que el listado GET normal ya no la devuelve.
7. Confirmar que `GET /api/v1/categories?include_inactive=true` sí la devuelve.
8. Intentar crear un duplicado con la categoría desactivada (falla con 409 Conflict).
9. Reactivar la categoría.
10. Confirmar su presencia en los listados regulares.

*Todos los tests de unidad (`pytest tests/`) y la suite completa de tests de humo de V1.1 pasaron exitosamente.*
