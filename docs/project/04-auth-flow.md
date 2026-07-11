# 04 — Authentication and Authorization

## Proveedor de Identidad
La autenticación de usuarios es delegada a **Supabase Auth**. El backend no almacena contraseñas ni hashes directamente.
- El cliente (Frontend/Móvil) obtiene un JWT (JSON Web Token) desde Supabase.
- El cliente envía este token al backend a través del header HTTP `Authorization: Bearer <token>`.

## Resolución de Identidad Interna
El backend cuenta con la dependencia `get_current_user` (en `app.api.deps` o similares).
Cuando un endpoint protegido es invocado:
1. El backend valida el JWT contra la firma/clave pública de Supabase.
2. Extrae el `sub` (Subject), que corresponde al `auth_id` externo.
3. Consulta internamente la tabla de usuarios mapeando el `auth_id` al ID interno robusto `users.id` de la base de datos de Nexum.

## Ownership y Acceso a Recursos
Toda la data financiera en el sistema (Cuentas, Obligaciones, Eventos Financieros, Metas) pertenece estrictamente a un `user_id`.
- Cuando se realiza una consulta (ej. `/api/v1.7/obligations/overview`), el servicio inyecta implícitamente el `user_id` del usuario autenticado.
- Cuando se consulta o muta un recurso por ID (ej. pagar una deuda específica), el backend valida que la deuda pertenezca al usuario (Ownership Check).

## Códigos de Error Típicos
- `401 Unauthorized`: Token faltante, expirado o inválido.
- `403 Forbidden`: El token es válido, pero el usuario intenta acceder o mutar un recurso que no le pertenece, o (temporalmente) choca con un Feature Flag que bloquea la ruta.

---
*Last verified against `367ebdc`*