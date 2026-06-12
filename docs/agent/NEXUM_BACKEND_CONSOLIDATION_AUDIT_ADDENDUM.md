# Nexum Backend Consolidation Audit — Corrections Addendum

## 1. Correcciones al audit original
Tras una inspección profunda a nivel de código y base de datos, se corrigen las siguientes imprecisiones del audit original:
- **Conversational Core incompleto:** El parser identifica intenciones complejas (Goals, Obligations, Credit), pero los "execute handlers" reales en `_execute_financial_action` no existen. Las acciones quedan en el limbo tras confirmarse.
- **RLS Bypass (Falso Positivo):** Aunque las políticas de RLS están configuradas en Supabase (`user_id = current_app_user_id()`), el backend de FastAPI accede a la base de datos a través de SQLAlchemy usando el Service Role / connection string directo, **bypasseando RLS a nivel de base de datos**. La seguridad actual depende 100% de los repositorios de Python filtrando por `user_id` en las consultas.
- **Transacciones Legacy seguras de eliminar:** Tras auditar dependencias en Postgres, la tabla `public.transactions` no tiene vistas materializadas ni foreign keys atadas a ella.

---

## 2. Conversational execution matrix
Revisión estricta de `app/conversations/service.py` (`_process_write_intent` y `_execute_financial_action`).

| Intent | Gemini | Resolver | Pending Action | Confirm | Ejecuta Dominio | Persiste Ledger |
|---|---|---|---|---|---|---|
| **create_income** | ✅ Sí | ✅ Sí | ✅ Sí | ✅ Sí | ✅ Sí (`cash_service`) | ✅ Sí |
| **create_expense** | ✅ Sí | ✅ Sí | ✅ Sí | ✅ Sí | ✅ Sí (`cash_service`) | ✅ Sí |
| **create_goal** | ✅ Sí | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No |
| **create_goal_cont.** | ✅ Sí | ✅ Sí | ✅ Sí | ✅ Sí | ❌ No (Roto) | ❌ No |
| **create_obligation** | ✅ Sí | ✅ Sí | ✅ Sí | ✅ Sí | ❌ No (Roto) | ❌ No |
| **create_oblig_pay** | ✅ Sí | ✅ Sí | ✅ Sí | ✅ Sí | ❌ No (Roto) | ❌ No |
| **create_cc_purchase**| ✅ Sí | ✅ Sí | ✅ Sí | ✅ Sí | ✅ Sí (`credit_service`) | ✅ Sí |
| **create_cc_payment** | ✅ Sí | ✅ Sí | ✅ Sí | ✅ Sí | ❌ No (Roto) | ❌ No |
| **account_transfer** | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No |
| **peer_loan** | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No |

**Conclusión:** Se asume que Goals, Obligations y Credit Payments funcionan conversacionalmente, pero están **ROTOS/INEXISTENTES** en el paso de ejecución final.

---

## 3. Clarification audit
**Roto por diseño defensivo.**
Flujo actual inspeccionado:
1. Mensaje incompleto → `awaiting_clarification` (Funciona).
2. Siguiente mensaje → El backend clasifica el mensaje entrante.
3. Al detectar que la acción estaba en `awaiting_clarification`, el backend la **cancela obligatoriamente** y devuelve el mensaje: *"La clarificación paso a paso aún está en desarrollo..."*.
4. Merge de entidades → **Inexistente**.

---

## 4. Traceability audit
Modelo de base de datos actual y relaciones revisadas:
- `messages.financial_event_id`: Aporta circularidad y rompe el modelo natural donde un mensaje precede al evento.
- `ai_runs`: Solo se vincula con `message_id`, no mapea `command_id`.
- `pending_actions.command_id`: Funciona para enlazar con `financial_events`.
- `financial_events.raw_message`: Presente y aprobado.

**Diseño Mínimo Propuesto:**
- `pending_actions` añade: `source_message_id` (el mensaje que disparó la acción) y `confirmation_message_id` (el mensaje del usuario diciendo "sí").
- `financial_events` añade: `source_message_id` (para ir directo al origen sin pasar por pending actions).
- Eliminar / Ignorar `messages.financial_event_id` en el código.

---

## 5. Legacy transactions audit
Auditoría directa en `pg_depend` y `pg_constraint`:
- **Datos existentes:** Sí (posiblemente de Fases previas).
- **Vistas dependientes:** Ninguna (`v_cashflow`, etc., leen de `financial_events`).
- **Foreign Keys:** Ninguna.
- **Triggers:** Ninguno.
- **Integraciones Frontend:** Ninguna llama a la ruta antigua de transactions.

**Clasificación:** **Eliminar solo cuando sea seguro.** (En un branch separado o como primera acción de Subfase 2A, haciendo backup preventivo de datos para no perder histórico antiguo).

---

## 6. RLS audit
**Políticas actuales:** Todas las tablas principales tienen `user_id = current_app_user_id()`.
**Inspección `current_app_user_id()`:** Es una función que mapea `auth.uid()` al ID interno `public.users.id`.
**Realidad:** El backend en FastAPI se conecta a través de un pool SQLAlchemy regular sin setear la variable de sesión Postgres (`set_config('request.jwt.claim.sub', ...)`).
**Riesgo:** El Service Role Bypass significa que RLS **no está filtrando a nivel de base de datos** para las llamadas originadas desde el backend en Python.
**Solución:** Continuar con validación por código en repositorios Python (ownership Python es obligatorio), y mantener RLS como medida exclusiva para conexiones directas desde el frontend a Supabase (si las hubiese).

---

## 7. Categories resolution design
**Diseño Mínimo:**
1. Categoría Global Canónica (ej. "Alimentación", pre-poblada en DB sin `user_id`).
2. Categoría Privada Visible (creada por usuario, ej. "Mis Antojos").
3. Alias (Ej. "comidita" -> map to ID).

**Flujo:**
1. Usuario escribe: *"gasté 20 mil en comidita"*.
2. Gemini extrae: `category_name="comidita"`.
3. Backend (Resolver):
   - Busca exact match en Aliases del usuario.
   - Si no, aplica distancia Levenshtein o Trigramas contra las categorías del usuario.
   - Si hay match alto, asigna `category_id`.
   - Si no hay match, Gemini pide clarificación: *"No encontré la categoría comidita, ¿quieres decir Alimentación?"*

---

## 8. Account Transfers design
- **Router/Domain:** `app/transfers`
- **Tablas:** No se requieren tablas nuevas.
- **Ejecución:** Un solo Command (Atómico, idempotente, usando un `command_id`).
- **Ledger Impact:**
  - Evento 1: `account_id=origen`, `direction=OUT`, `event_type=transfer`, `amount=X`.
  - Evento 2: `account_id=destino`, `direction=IN`, `event_type=transfer`, `amount=X`.
- Las vistas de inteligencia omiten `event_type='transfer'` del sumatorio de Income/Expense.

---

## 9. Peer Loans design
**Dominio:** `app/peer_loans`
MVP (Préstamos informales):
- **Externo:** Nexum Usuario a No-Nexum (Amigo). Se maneja como una Cuenta/Bolsillo virtual de tipo "Préstamo a Tercero" (transferencia OUT a esta cuenta). El dinero sigue siendo tuyo (net worth) pero no está disponible.
- **Nexum ↔ Nexum:** 
  1. Usuario A inicia transferencia a Usuario B (por email/alias).
  2. Queda en estado `pending_acceptance`.
  3. Usuario B acepta, eligiendo su cuenta destino en Nexum.
  4. Transacción atómica: OUT de Cuenta A, IN de Cuenta B. Registro en tabla `loan_agreements` para historial y repago.

---

## 10. Goals gaps
- create / contribution: Chat parser funciona, pero execution `_execute_financial_action` inexistente. API directo (router) funciona.
- Historial de meta: Ausente (no hay timeline de contribuciones).
- Retiro/Reversión: Inexistente.
- Contribución planificada (auto): Inexistente.
- Pausa/reactivación: Inexistente en API.

---

## 11. Obligations gaps
- create: Execution handler en chat roto.
- payment: Execution handler roto.
- Recurrencias: Inexistente (requeriría CRON o worker asíncrono).
- Pago parcial / superior / Historial: Incompleto, no hay validación profunda de sobrepago en API.

---

## 12. Credit gaps
- create_purchase: Implementado en API y chat.
- create_payment: Falta handler de ejecución en chat.
- Cuotas/Corte: Lógica incipiente, requiere auditoría matemática para deuda real vs proyectada.
- Historial / Saldo a favor: Inexistente.

---

## 13. Financial History design
- **Endpoint:** `GET /api/v1/ledger`
- **Response:** Paginado (cursor o offset/limit), filtrable por `account_id`, `date_range`, `event_type`.
- **Regla:** Mostrar últimos 60 días por defecto en frontend.
- **Soporte de Reversiones:** Soft-delete de `financial_events` o generación de evento compensatorio (recomendado: Evento Compensatorio con `metadata: {reverted_command_id: UUID}`).

---

## 14. Migraciones mínimas propuestas
- Eliminar `transactions`.
- Añadir `source_message_id` (UUID) y `confirmation_message_id` (UUID) a `pending_actions`.
- Añadir `source_message_id` (UUID) a `financial_events`.
- (Opcional) Eliminar/Ignorar el constraint de `messages.financial_event_id` para romper circularidad.

---

## 15. Roadmap corregido

**Fase B — Conversational Core + Traceability**
- Completar los `execute_handlers` perdidos (Goals, Obligations).
- Reparar Clarification flow.
- Desplegar modelo final de Traceability.

**Fase C — Financial History + Reversions**
- Endpoint `/api/v1/ledger`.
- Reversiones idempotentes.

**Fase D — Accounts + Categories + Transfers**
- Account transfers inter-cuentas.
- Resolución determinística de categorías.

**Fase E — Goals + Obligations Advanced**
- Recurrencias, pagos parciales, saldos y retiros.

**Fase F — Credit Advanced**
- Auditoría matemática de TC, fechas de corte y pagos de cuotas de manejo.

**Fase G — Peer Loans**
- Préstamos a terceros informales y Nexum ↔ Nexum.

**Fase H — Intelligence + Insights deterministas**
**Fase I — Onboarding + Seguridad + Analytics + Admin**

---

## 16. Recomendación de primera fase de implementación
**No arrancar construyendo features nuevas (Fases D-G).**
La acción inmediata debe ser la **Fase B: Completar los execution handlers del Conversational Core** (Goals y Obligations que actualmente caen al vacío tras ser confirmados) y aplicar las **Migraciones de Trazabilidad**. Un bot que confirma pero no ejecuta es el peor escenario para la confianza del usuario final.
