# Nexum V1.5 Recovery Lock

## 1. Executive Summary
Este documento certifica que el entorno de producción de Nexum ha sido recuperado y estabilizado exitosamente en la versión V1.5 tras el rollback de emergencia desde la rama V1.6/V1.6.2. Todas las incompatibilidades críticas causadas por el desajuste entre el código V1.5 y la base de datos V1.6 han sido resueltas. 

## 2. Current State
- **Backend Runtime**: `ca7b165` (V1.5)
- **Frontend Release**: `2f6f404` (release/v1.5-rollback)
- **Database**: V1.5 Compatible (Esquema híbrido seguro con dependencias V1.5 restauradas y constraints de V1.6 relajados)

## 3. Post-Rollback Fixes Applied
1. **Auth Recovery**: Eliminación de usuario fantasma desincronizado entre Supabase Auth y `public.users` que bloqueaba el login/signup.
2. **Database Stabilization (Reads)**: 
   - Restauración de columnas deprecadas por V1.6 en `obligations` (`amount`, `is_active`).
   - Restauración de columnas deprecadas en `obligation_payments` (`user_id`, `period`, `event_id`, etc.).
   - Recreación de la vista legacy requerida por Inteligencia: `v_pending_obligations_current_month`.
3. **Database Stabilization (Writes)**: 
   - Eliminación temporal de restricciones `NOT NULL` huérfanas de V1.6 en `obligations` (`base_amount`, `type`, `start_date`, etc.) y `obligation_payments` (`currency`, `source_amount`, etc.) para permitir el flujo `INSERT` nativo de V1.5.

## 4. Functional Status
- **Dashboard**: 100% Funcional (Endpoint `/api/v1/intelligence/snapshot` retorna 200 OK).
- **Obligations List**: 100% Funcional (Endpoint `/api/v1/obligations` retorna 200 OK).
- **Obligations Create**: 100% Funcional (Endpoint POST retorna 201/200 OK sin bloqueos de base de datos).
- **Logs de Producción**: Limpios, sin `UndefinedColumnError` ni `NotNullViolationError` y libres de tracebacks o errores críticos.

## 5. Residual Risks
La relajación de las restricciones estructurales significa que los registros de obligaciones y pagos creados durante este periodo operativo (V1.5) tendrán valores `NULL` en las columnas que pertenecen exclusivamente a V1.6 (ej. `base_amount`, `obligation_period_id`). 
Cuando el desarrollo avance a la siguiente versión, será obligatoria la ejecución de una migración de datos (data migration) para sanitizar y rellenar estos valores antes de poder restaurar de forma segura las restricciones `NOT NULL`.

## 6. Recommendations
- **Producción bloqueada**: Se recomienda estrictamente NO realizar más modificaciones en el código de producción ni en la base de datos bajo este runtime (V1.5). El sistema se encuentra estable para uso del usuario final.
- **Next Phase**: Se debe iniciar un esfuerzo estructurado y limpio denominado **"Core Obligations V1.7 Safe Rebuild"**, partiendo de un entorno local/staging purgado para evitar la repetición de colisiones de esquema como las experimentadas con V1.6.
