# Este prompt se envía a Gemini
SYSTEM_PROMPT_TEMPLATE = """
Eres Nexum, un asistente financiero experto.
Tu ÚNICA tarea es extraer la intención del usuario y las entidades nombradas desde el mensaje.
Devuelve los resultados ESTRICTAMENTE en formato JSON validando el esquema esperado.

Reglas:
1. No calcules saldos ni operaciones matemáticas.
2. Si el usuario pregunta por saldo, asigna intent "ask_balance".
3. Si el usuario registra un gasto, asigna intent "create_expense". Extrae 'amount', 'category' (si aplica), 'account' (si aplica).
4. Si el usuario registra un ingreso, asigna intent "create_income".
5. Si el usuario indica que movió o transfirió dinero de una cuenta propia a otra cuenta propia (ej. "pasé 100 mil de Nequi a Bancolombia"), asigna intent "create_transfer". Extrae 'amount', 'source_account' y 'destination_account'.
6. Si el usuario confirma algo anterior, asigna "confirm_action".
7. Si cancela algo, asigna "cancel_action".
8. No asumas entidades si no están mencionadas.
9. Para 'amount', usa números puros sin comas separadoras de miles ni símbolos. (ej. 50000).
10. Extrae SIEMPRE TODAS las entidades mencionadas (account, source_account, destination_account, goal, obligation, credit_card, category, amount, etc.) sin importar la intención.

Opciones disponibles para contexto:
Cuentas: {accounts}
Categorías: {categories}
Tarjetas de Crédito: {credit_cards}
Metas: {goals}
Obligaciones: {obligations}

Recuerda: Si el texto del usuario menciona alguna cuenta/tarjeta/meta/categoría de forma inexacta, 
extrae literalmente cómo la llamó el usuario. El sistema se encargará de resolver el nombre exacto.
"""


def build_system_prompt(
    accounts: list[str],
    categories: list[str],
    credit_cards: list[str],
    goals: list[str],
    obligations: list[str],
) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        accounts=", ".join(accounts) if accounts else "Ninguna",
        categories=", ".join(categories) if categories else "Ninguna",
        credit_cards=", ".join(credit_cards) if credit_cards else "Ninguna",
        goals=", ".join(goals) if goals else "Ninguna",
        obligations=", ".join(obligations) if obligations else "Ninguna",
    )


# ────────────────────────────────────────────────────────────────────────────
# Plantillas estáticas para responder lecturas
# ────────────────────────────────────────────────────────────────────────────
def render_read_response(intent: str, data: dict) -> str:
    if intent == "ask_balance":
        return f"Tu saldo disponible real es de ${data.get('total_available_real', '0.00')}."
    elif intent == "ask_free_money":
        return f"Tienes ${data.get('free_money', '0.00')} de dinero libre."
    elif intent == "ask_debt":
        cards_info = []
        for cc in data.get("credit_cards", []):
            cards_info.append(
                f"- {cc.get('credit_card_name')}: Deuda actual ${cc.get('current_debt', cc.get('estimated_current_debt', '0.00'))} "
                f"(Facturado: ${cc.get('billed_debt', '0.00')}, Sin facturar: ${cc.get('unbilled_debt', '0.00')}). "
                f"Pago requerido: ${cc.get('payment_required', '0.00')}. "
                f"Estimado próximo pago: ${cc.get('next_payment_estimate', '0.00')}. "
                f"Cupo disponible: ${cc.get('available_credit', cc.get('estimated_available_credit', '0.00'))}. "
                f"Próximo pago: {cc.get('next_payment_due_date', 'N/A')}."
            )
        cards_str = "\\n".join(cards_info) if cards_info else "No tienes tarjetas registradas."
        return f"Tu deuda total de tarjetas de crédito es de ${data.get('total_estimated_credit_card_debt', '0.00')}.\\nDetalle:\\n{cards_str}"
    elif intent == "ask_cashflow":
        return (
            f"Resumen de flujo de caja del mes:\n"
            f"Ingresos: ${data.get('income', '0.00')}\n"
            f"Consumo: ${data.get('total_consumption_committed', '0.00')}\n"
            f"Flujo neto: ${data.get('net_cashflow', '0.00')}"
        )
    elif intent == "ask_goals":
        return f"Tus aportes requeridos a metas este mes son ${data.get('goals_required_this_period', '0.00')}."
    elif intent == "ask_obligations":
        return (
            f"Tus obligaciones pendientes suman ${data.get('pending_obligations_total', '0.00')}."
        )
    elif intent == "ask_financial_snapshot":
        return (
            f"Aquí está tu resumen financiero:\n"
            f"Dinero libre: ${data.get('free_money', '0.00')}\n"
            f"Dinero seguro: ${data.get('safe_money', '0.00')}\n"
            f"Deuda TC: ${data.get('total_credit_card_debt', '0.00')}\n"
            f"Obligaciones pendientes: ${data.get('pending_obligations_total', '0.00')}"
        )
    return "Aquí está la información solicitada."
