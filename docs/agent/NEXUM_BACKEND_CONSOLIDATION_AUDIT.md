# Nexum Backend Consolidation Audit — MVP V2 Pre-Implementation

## 1. Resumen ejecutivo
Nexum ha alcanzado un MVP técnico funcional, pero con áreas clave que requieren maduración antes de agregar nuevas funcionalidades de alto nivel. La arquitectura base (FastAPI, Supabase, Gemini) es sólida y modular. La máquina de estados conversacional funciona para flujos felices, pero la trazabilidad entre intenciones y eventos ejecutados es frágil. El frontend (Next.js) se conecta a producción, pero carece de varias interfaces fundamentales (historial, ajustes), delegando todo al chat. El esquema de base de datos es robusto, pero acumula deuda técnica menor (tablas deprecadas como `transactions`). Se requiere consolidar el MVP actual (V1.5) hacia una V2 sólida antes de abordar `Peer Loans` o `Account Transfers`.

## 2. Estado real del backend
- **Framework:** FastAPI con inyección de dependencias y routers por dominio.
- **Dominios expuestos:** `users`, `accounts`, `categories`, `cash`, `goals`, `obligations`, `credit`, `intelligence`, `conversations`.
- **Dominios no expuestos/ausentes:** No existe un router directo para `ledger` (se accede vía `cash` y chat), no hay endpoints de `Account Transfers` ni `Peer Loans`.
- **Estado operativo:** Los tests pasan (118+ pruebas). El manejo de errores y CORS está configurado y probado.

## 3. Matriz implementado / parcial / roto / inexistente
- **Implementado:** Autenticación JWT Supabase, Health/Readiness, Cash (Income/Expense), Intelligence Queries (Balance, Free Money, Snapshot), Conversational Core (Confirm/Cancel).
- **Parcial:** Goals (faltan aportes planificados), Obligations (falta historial detallado), Credit (funcional pero requiere validación financiera en cuotas).
- **Roto:** Clarificaciones interactivas en el chat (desactivadas defensivamente).
- **Inexistente:** Historial Financiero (Ledger endpoint paginado), Account Transfers, Peer Loans, Product Analytics, Admin Dashboard.

## 4. Estado real del frontend conectado
- **Usa API Real:** Autenticación (Supabase Auth UI), Onboarding de cuenta y categoría, Dashboard (Intelligence Snapshot, Balance, Free Money, Cashflow), y Chat (Conversations).
- **Hardcodeado / Falso:** Varios textos y lógicas de error genéricas. El dashboard asume datos a partir del snapshot pero carece de vistas profundas.
- **Preservación de Estado:** El chat *sí* preserva `pending_action_id` y genera `external_message_id` únicos por intento (corregido en el último parche).
- **Gaps Críticos:** No hay UI para listar/ver historial de cuentas. No hay settings. Si el usuario quiere ver sus metas, depende 100% de que el chat entienda su petición.

## 5. Esquema Supabase real
- **Tablas Activas:** `users`, `accounts`, `categories`, `credit_cards`, `credit_card_transactions`, `obligations`, `obligation_payments`, `goals`, `goal_contributions`, `financial_events`, `messages`, `ai_runs`, `pending_actions`, `idempotency_keys`, `logs`.
- **Tablas Legacy:** `transactions` (Deprecada. Funciona mediante `financial_events`).
- **Vistas Materializadas / Lógicas:** `v_account_balances`, `v_cashflow_current_month`, `v_financial_snapshot_current_month`, `v_credit_card_debt`, etc. Las vistas resuelven agregaciones al vuelo de `financial_events`.

## 6. Trazabilidad actual
1. **Relaciones que existen:** `financial_events.command_id` -> `pending_actions.command_id`. `messages.external_message_id`. `ai_runs.message_id` -> `messages.id`.
2. **Cuáles se llenan:** `pending_actions.command_id` y `financial_events.command_id` coinciden, permitiendo relacionar un evento ejecutado con la acción.
3. **Cuáles están vacías:** `messages.financial_event_id` y `ai_runs.financial_event_id` casi nunca se enlazan retroactivamente.
4. **Faltas:** No hay un link directo `messages -> financial_events` sin pasar por `pending_actions`.

## 7. Trazabilidad propuesta
1. Eliminar/ignorar `messages.financial_event_id` por generar circularidad (el mensaje causa el pending action, que luego se ejecuta).
2. Trazabilidad canónica: `Message` (con su `external_message_id`) → dispara un `ai_run` → crea un `pending_action` con `command_id` y lo vincula al `message_id` origen.
3. Al confirmar: El `financial_event` hereda el `command_id` del `pending_action`, y captura `raw_message` con el prompt original del usuario (no de la confirmación).

## 8. Conversational Core
- **Confirmación natural:** FUNCIONA.
- **Cancelación:** FUNCIONA.
- **Ambigüedad / Fallbacks:** FUNCIONA (retiene contexto).
- **Clarification:** PARCIAL/ROTO. Desactivado temporalmente, cancela la acción indicando que la "clarificación paso a paso está en desarrollo".
- **Deduplicación & Idempotencia:** FUNCIONA (usa `external_message_id` y `command_id`).

## 9. Cash
- Implementado y estable (`create_income`, `create_expense`). Impacta `financial_events` y actualiza vistas.

## 10. Goals
- Parcialmente implementado. Permite crear y registrar aportes manuales, pero faltan lógicas automatizadas, aportes planificados, y la interfaz de clarificación en chat para destinar "dinero restante a metas".

## 11. Obligations
- Parcialmente implementado. Permite crear y pagar. Carece de historial detallado por obligación y control estricto de vencimientos.

## 12. Credit
- Implementado a nivel esquema (`credit_cards`, `credit_card_transactions`), pero complejo. La lógica de interés compuesto y división de cuotas es matemática financiera que requiere auditoría de Edge Cases (pagos parciales, cuotas de manejo).

## 13. Intelligence Queries
- Implementado. Los endpoints (`/snapshot`, `/balance`, `/cashflow`, etc.) devuelven data correcta leyendo de las vistas de Supabase.

## 14. Categories
- **Estado:** CRUD básico implementado.
- **Problema de resolución:** El chat delega 100% en Gemini.
- **Solución Propuesta:** Adoptar similitud de strings (ej. Levenshtein o trigramas PostgreSQL) o embeddings locales para mapear "Comidita" a la categoría canónica "Alimentación" antes de insertar.

## 15. Financial History
- **Inexistente.** No hay un endpoint `/ledger/history` paginado.
- Actualmente, el usuario solo ve totales agregados. Necesitamos un endpoint `GET /api/v1/ledger` que liste `financial_events` (offset, limit) para que el frontend despliegue una tabla.

## 16. Account Transfers
- **Inexistente.** Se requiere el dominio `account_transfer`.
- Consiste en registrar 2 `financial_events` bajo la misma transacción SQL y `command_id`: Un gasto en cuenta origen (dirección OUT) y un ingreso en destino (dirección IN), con `event_type = 'transfer'`. No afecta el `consumption_rate` ni `income_total`.

## 17. Peer Loans
- **Inexistente.**
- Requiere una arquitectura extensa: Estado de invitaciones, validaciones de saldo, y contabilidad de doble entrada entre dos usuarios de Nexum, además de registrar promesas de pago externas. Debe separarse como una épica completa post-MVP.

## 18. Seguridad
- **RLS (Row Level Security):** Aplicado en todas las tablas (`user_id = auth.uid()`).
- **Auth:** Funcional vía Supabase JWT.
- **Variables Sensibles:** Archivos remotos sanitizados. `.env` no commiteado.
- **CORS:** Configurado para `api.nexum.lytrium.tech` y `localhost:3000`.

## 19. Analytics
- **Inexistente.** Falta un modelo mínimo para medir tokens consumidos vs retención de usuarios.

## 20. Admin
- **Inexistente.** No existe panel de control para Super Admins, indispensable antes del Beta Público.

## 21. Post-MVP backlog
- Peer Loans (Préstamos sociales y externos).
- Account Transfers.
- Machine Learning (Categorización proactiva sin Gemini para ahorrar tokens).
- Notificaciones WhatsApp/Push.
- Admin y Analytics Dashboards.

## 22. Migraciones mínimas propuestas
1. Borrar la tabla legacy `transactions` (para evitar confusión de arquitectos futuros).
2. Añadir `source_message_id` a `pending_actions` para mejorar la trazabilidad sin circularidades.

## 23. Riesgos
- **Opacidad del Ledger:** Si un usuario se equivoca al ingresar un monto, no tiene interfaz gráfica ni endpoint para ver o revertir el movimiento histórico.
- **Costo Gemini:** Falta de categorización determinística significa que cada mensaje insignificante gasta tokens de razonamiento.

## 24. Roadmap por subfases
**Subfase 2A: Consolidación Visual (Próxima)**
1. Endpoint paginado de Financial History (`GET /api/v1/ledger`).
2. Pantalla de Historial en Frontend.
3. Corrección de vistas (eliminar tabla `transactions`).

**Subfase 2B: Consolidación Transaccional**
1. Implementar `Account Transfers` (Backend).
2. Añadir similitud de categorías determinística.
3. Reactivar "Clarificaciones Interactivas" en el chat.

**Subfase 2C: Advanced Features (Beta)**
1. Peer Loans MVP.
2. WhatsApp Bot Integration.

## 25. Recomendación final
Nexum MVP está en una **Alpha Privada estable**.
NO se debe iniciar con Peer Loans o Account Transfers hasta no implementar el endpoint paginado del `Financial History` (Ledger) y exponerlo en el frontend. Un sistema financiero sin un historial visible carece de la confianza mínima viable del usuario final.
