# Contratos API

Los contratos completos pueden consultarse en openapi.json o /docs. Todos los endpoints de dominios financieros y de IA requieren el header Authorization: Bearer <TOKEN>.

## Multi-Currency V1.4 Contract Details
- **Creación de Entidades**: Todo POST/creación en `accounts`, `credit-cards`, `transfers`, `goals`, `obligations` y `financial_events` exige explícitamente el campo `currency` de 3 letras.
- **SnapshotTruth**: Cuando hay múltiples monedas, los totales globales como `available_real` retornan `0` (Zeroed) para evitar sumas financieras matemáticamente inválidas.
- **totals_by_currency**: La fuente de verdad financiera en `GET /intelligence/snapshot`. Retorna un objeto por cada moneda encontrada.
- **estimated_totals**: Capa de estimación visual unificada en el `IntelligenceSnapshotRead`. Convierte los montos a `base_currency` (COP) usando el proveedor configurado (DolarAPI). Contiene `is_estimated = True` como garantía.