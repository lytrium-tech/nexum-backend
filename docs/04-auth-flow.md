# Flujo de Autenticación

1. El usuario se registra/hace login vía Supabase Client (Frontend).
2. El frontend envía el JWT (Access Token) al backend.
3. El backend verifica la firma del JWT usando JWKS de Supabase.
4. Si el usuario no existe en public.users, el frontend debe llamar a POST /api/v1/users/me/bootstrap.
5. El backend resuelve todas las consultas financieras vinculando el ID interno public.users.id derivado del JWT.