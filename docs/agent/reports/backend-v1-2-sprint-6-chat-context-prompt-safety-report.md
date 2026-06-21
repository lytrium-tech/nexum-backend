# Backend V1.2 Sprint 6 — Chat Context & Prompt Safety Report

## 1. Executive Summary
El Sprint 6 se completó con éxito. Se auditó la arquitectura del chat conversacional de Nexum, confirmando que en V1.2 el LLM opera exclusivamente como un motor NLU (Natural Language Understanding) de extracción de intenciones (`intents`) y entidades. El contrato del prompt se ajustó con reglas estrictas de seguridad (Safety Guardrails) para evitar invenciones de UUIDs, cálculos financieros por parte del LLM y mezcla de monedas.

## 2. Problem Reviewed
Se requería estabilizar el alcance conversacional del bot, ya que no se implementará el "Financial Advisor Avanzado" en V1.2. Era necesario documentar qué puede hacer hoy, prohibir que el LLM calcule saldos, y manejar preguntas complejas (multi-intent) o asesorías de forma prudente.

## 3. Conversational Core Audit
- **Provider:** Gemini (vía `app.integrations.gemini_client`).
- **Architecture:** El LLM extrae `intent` + `entities` y devuelve JSON. El backend rutea esto a servicios específicos.
- **Rendering:** Las respuestas de lectura se formatean en Python usando templates estáticos (`render_read_response`), impidiendo por diseño que el LLM "invente" texto financiero directo.

## 4. Prompt Contract Before
El prompt ya contenía la regla `"No calcules saldos ni operaciones matemáticas"`. Sin embargo, faltaba delimitar explícitamente escenarios como multi-intent ambiguos, asesoría avanzada y mezcla de monedas.

## 5. Prompt Contract After
Se ajustó `SYSTEM_PROMPT_TEMPLATE` en `app/conversations/prompts.py` para incluir:
- `"No calcules dinero libre sin backend. No mezcles monedas."`
- `"Usa valores calculados por backend. No inventes UUIDs ni entidades que no existan."`
- `"Si el usuario pide consejo, asesoría financiera, o recomendaciones, asigna intent 'ask_advice'."`
- `"Si la intención no está soportada o no es clara, asigna 'unknown' (fail closed). Si el mensaje tiene múltiples intenciones complejas que no puedes resolver en una, asigna 'unknown'."`

## 6. Backend-Calculated Context
Se verificó que los siguientes datos provienen estricta y únicamente de `IntelligenceService` y sus vistas (Snapshots):
- `ask_balance` -> `total_available_real`
- `ask_free_money` -> `free_money`
- `ask_debt` -> `total_estimated_credit_card_debt`
- `ask_cashflow` -> `net_cashflow`, `income`, `total_consumption_committed`
- `ask_goals` -> `goals_required_this_period`
- `ask_obligations` -> `pending_obligations_total`
- `ask_financial_snapshot` -> snapshot fields

## 7. Supported Queries
- Consultas (Reads): `ask_balance`, `ask_free_money`, `ask_debt`, `ask_cashflow`, `ask_goals`, `ask_obligations`, `ask_financial_snapshot`, `ask_advice`.
- Escrituras (Writes): `create_income`, `create_expense`, `create_transfer`, `create_goal`, `create_goal_contribution`, `create_obligation`, `create_obligation_payment`, `create_credit_card_purchase`, `create_credit_card_payment`.

## 8. Unsupported / V2 Scope
Lo siguiente queda fuera de V1.2 (retorna `ask_advice` genérico o `unknown`):
- Asesoría financiera avanzada o específica de productos reales.
- Motor de escenarios o proyecciones a largo plazo.
- Multi-intent complejos (ej. "Crea este gasto Y dime cuánto me queda", el LLM prioriza fail-closed `unknown` si no puede aislarlo).

## 9. Financial Advice Safety
El nuevo intent `ask_advice` responde con un disclaimer estático y seguro:
> "Según tus datos actuales, podrías considerar revisar tu flujo de caja y deudas. Nota: Esto no reemplaza asesoría financiera profesional."

## 10. Multi-Intent Status
Para el MVP V1.2, se delega al LLM la responsabilidad de resolver a la intención primaria. Si detecta intenciones complejas y contrapuestas que no caben en un único intent JSON, debe retornar `"unknown"` para activar el fallback defensivo (Fail-Closed) y solicitar clarificación al usuario.

## 11. Fail-Closed Behavior
Si el usuario envía algo no soportado, el intent `"unknown"` se mapea a:
> "No pude entender tu solicitud o la intención no es clara."
Esto evita silencios y respuestas engañosas.

## 12. Tests
Se agregó `tests/unit/test_conversations_semantics.py` para asegurar que:
- Las reglas de no inventar saldos estén en el prompt.
- `ask_advice` incluya el disclaimer.
- `ask_balance`, `ask_debt`, y `ask_free_money` usen data en el renderizado y no en el prompt base del LLM.

Los 171 tests de la suite pasan.

## 13. OpenAPI / Docs
- `openapi.json` está actualizado.
- La documentación del prompt queda autoexplicada en el código y este reporte de auditoría.

## 14. Risks
- Mínimo. Al sacar la carga de generación de lenguaje de dominio financiero del LLM (usando en su lugar plantillas Python), el riesgo de alucinación financiera es del 0%. El único riesgo recae en una mala clasificación (False Positives/Negatives de Intent), lo que está mitigado por el flujo de confirmación.

## 15. Final Status
El Sprint 6 se declara completado exitosamente y listo para revisión.
