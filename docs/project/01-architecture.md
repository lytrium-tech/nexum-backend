# Arquitectura

- **Framework**: FastAPI
- **DB**: PostgreSQL (Aislada)
- **Auth**: Supabase JWT (Validado asimétricamente vía JWKS)
- **Patrón**: Arquitectura Hexagonal (Routers -> Services -> Unit of Work -> Repositories)
- **IA**: Integración con Google Gemini.