import logging
import re
import unicodedata
import uuid
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.cash.schemas import CashExpenseCreate, CashIncomeCreate
from app.cash.service import CashService
from app.conversations.entity_resolver import (
    AmbiguousEntityError,
    EntityNotFoundError,
    resolve_entity,
)
from app.conversations.exceptions import UnsupportedConversationalIntentError
from app.conversations.intent_catalog import (
    is_read_intent,
    is_write_intent,
)
from app.conversations.prompts import build_system_prompt, render_read_response
from app.conversations.repository import ConversationsRepository
from app.conversations.schemas import (
    ConversationalRequest,
    ConversationalResponse,
    PendingActionRead,
)
from app.credit.schemas import CreditCardPaymentCreate, CreditCardPurchaseCreate
from app.credit.service import CreditCardService
from app.goals.schemas import GoalContributionCreate, GoalCreate
from app.goals.service import GoalService
from app.integrations.gemini_client import gemini_client
from app.intelligence.service import IntelligenceService
from app.obligations.schemas import ObligationCreate, ObligationPaymentCreate
from app.obligations.service import ObligationService

logger = logging.getLogger(__name__)

def classify_pending_reply(text: str) -> Literal["confirm", "cancel", "unknown"]:
    text_lower = text.lower().strip()
    text_lower = unicodedata.normalize('NFD', text_lower).encode('ascii', 'ignore').decode('utf-8')
    text_lower = re.sub(r'[^\w\s]', '', text_lower)
    text_lower = re.sub(r'\s+', ' ', text_lower).strip()

    confirm_phrases = {"si", "ok", "confirmo", "si confirmo", "si lo confirmo", "confirmar", "dale", "claro", "de acuerdo", "adelante", "hazlo", "si adelante", "si claro", "si hazlo"}
    cancel_phrases = {
        "no", "cancelar", "cancela", "olvidalo", "no confirmo", "mejor no", "no lo hagas", "detener", "no mejor no",
        "cancelar todo", "ninguna", "olvida eso", "descartar", "empezar de nuevo"
    }

    if text_lower in confirm_phrases:
        return "confirm"
    if text_lower in cancel_phrases:
        return "cancel"
    return "unknown"

class ConversationsService:
    def __init__(
        self, 
        session: AsyncSession,
        intel_service: IntelligenceService,
        cash_service: CashService,
        goals_service: GoalService,
        obl_service: ObligationService,
        credit_service: CreditCardService,
    ):
        self.session = session
        self.repo = ConversationsRepository(session)
        self.intel_service = intel_service
        self.cash_service = cash_service
        self.goals_service = goals_service
        self.obl_service = obl_service
        self.credit_service = credit_service

    async def handle_message(
        self, user_id: uuid.UUID, request: ConversationalRequest, trace_id: uuid.UUID
    ) -> ConversationalResponse:
        
        # 1. Deduplicación por external_message_id
        if request.external_message_id:
            existing = await self.repo.get_message_by_external_id(request.channel, request.external_message_id)
            if existing:
                logger.info(f"Deduplicated message {request.external_message_id}")
                resp_data = existing["response_data"]
                if isinstance(resp_data, str):
                    import json
                    resp_data = json.loads(resp_data)
                return ConversationalResponse(**resp_data)

        # 2. Expirar pending actions viejas
        await self.repo.mark_expired_pending_actions(user_id)
        open_actions = await self.repo.get_open_pending_actions(user_id)

        # 3. Límite de acciones abiertas (máx 3)
        if len(open_actions) > 3:
            for act in open_actions[:-3]:
                await self.repo.update_pending_action_status(act.id, "cancelled")
            open_actions = open_actions[-3:]

        classification = classify_pending_reply(request.message)

        # 4. Cancelación global
        if open_actions and classification == "cancel":
            await self.repo.cancel_open_pending_actions(user_id)
            inbound_msg_id = await self.repo.save_message(
                user_id=user_id, channel=request.channel, direction="inbound", role="user",
                message_text=request.message, external_message_id=request.external_message_id, trace_id=trace_id
            )
            return await self._build_and_save_response(
                user_id, inbound_msg_id, request, "Listo, cancelé las operaciones pendientes. Puedes empezar de nuevo.",
                "unknown", "cancelled", trace_id
            )

        # 5. Selección por número
        if not request.pending_action_id and len(open_actions) > 1:
            try:
                sel_num = int(request.message.strip())
                if 1 <= sel_num <= len(open_actions):
                    selected_action = open_actions[sel_num - 1]
                    
                    for act in open_actions:
                        if act.id != selected_action.id:
                            await self.repo.update_pending_action_status(act.id, "cancelled")
                    
                    inbound_msg_id = await self.repo.save_message(
                        user_id=user_id, channel=request.channel, direction="inbound", role="user",
                        message_text=request.message, external_message_id=request.external_message_id, trace_id=trace_id
                    )
                    
                    if selected_action.status == "awaiting_confirmation":
                        prompt = self._generate_confirmation_text(selected_action.intent, selected_action.data)
                    else:
                        prompt = list(selected_action.missing_fields.values())[0]
                        
                    return await self._build_and_save_response(
                        user_id, inbound_msg_id, request, prompt,
                        selected_action.intent, selected_action.status, trace_id, pending_action_id=selected_action.id
                    )
            except ValueError:
                pass

        # 6. Flujo con pending_action_id
        if request.pending_action_id:
            return await self._handle_pending_action_flow(user_id, request, trace_id, open_actions)

        # 7. Fallback defensivo si solo hay 1 acción y no mandaron ID
        if len(open_actions) == 1:
            logger.info(f"Fallback defensivo aplicado: asociando respuesta a pending_action_id {open_actions[0].id}")
            request.pending_action_id = str(open_actions[0].id)
            return await self._handle_pending_action_flow(user_id, request, trace_id, open_actions)

        # 8. Mensaje nuevo (0 abiertas, o varias abiertas con mensaje ambiguo/nuevo intent)
        inbound_msg_id = await self.repo.save_message(
            user_id=user_id,
            channel=request.channel,
            direction="inbound",
            role="user",
            message_text=request.message,
            external_message_id=request.external_message_id,
            trace_id=trace_id
        )

        accounts = await self.cash_service.account_repo.list_by_user(user_id)
        categories = await self.cash_service.category_repo.list_available(user_id)
        credit_cards = await self.credit_service.repo.get_all_for_user(user_id)
        goals = await self.goals_service.repository.list_active(user_id)
        obligations = await self.obl_service.repository.list_active(user_id)

        prompt = build_system_prompt(
            accounts=[a.name for a in accounts],
            categories=[c.name for c in categories],
            credit_cards=[cc.name for cc in credit_cards],
            goals=[g.name for g in goals],
            obligations=[o.name for o in obligations]
        )

        nlu_out, metadata = gemini_client.extract_intent_and_entities(
            message=request.message,
            system_prompt=prompt,
        )

        await self.repo.save_ai_run(
            user_id=user_id,
            message_id=inbound_msg_id,
            provider="gemini",
            model=metadata["model"],
            input_data={"message": request.message},
            output_data=nlu_out.model_dump(mode="json"),
            intent=nlu_out.intent,
            success=metadata["success"],
            prompt_tokens=metadata["prompt_tokens"],
            completion_tokens=metadata["completion_tokens"],
            total_tokens=metadata["total_tokens"],
            latency_ms=metadata["latency_ms"],
            trace_id=trace_id
        )

        intent = nlu_out.intent

        if is_read_intent(intent):
            return await self._process_read_intent(user_id, intent, request, inbound_msg_id, trace_id)

        if is_write_intent(intent):
            return await self._process_write_intent(
                user_id, intent, nlu_out.entities, request, inbound_msg_id, trace_id,
                accounts, categories, credit_cards, goals, obligations,
                open_actions_to_cancel=open_actions if len(open_actions) > 1 else None
            )
        
        # Default / Fallback o Ambiguo
        if len(open_actions) > 1:
            return await self._build_and_save_multiple_actions_response(user_id, inbound_msg_id, request, trace_id, open_actions)

        return await self._build_and_save_response(
            user_id, inbound_msg_id, request, "No pude entender tu solicitud o la intención no es clara.",
            "unknown", "completed", trace_id
        )

    async def _process_read_intent(
        self, user_id: uuid.UUID, intent: str, req: ConversationalRequest, msg_id: uuid.UUID, trace_id: uuid.UUID
    ) -> ConversationalResponse:
        if intent == "ask_balance":
            data = await self.intel_service.get_balance(user_id)
        elif intent == "ask_free_money":
            data = await self.intel_service.get_free_money(user_id)
        elif intent == "ask_debt":
            data = await self.intel_service.get_debt(user_id)
        elif intent == "ask_cashflow":
            data = await self.intel_service.get_cashflow_current_month(user_id)
        elif intent == "ask_goals":
            data = await self.intel_service.get_goals_requirements(user_id)
        elif intent == "ask_obligations":
            data = await self.intel_service.get_obligations_requirements(user_id)
        elif intent == "ask_financial_snapshot":
            data = await self.intel_service.get_financial_snapshot(user_id)
        else:
            data = {}

        data_dict = data.model_dump(mode="json") if hasattr(data, "model_dump") else {}
        response_text = render_read_response(intent, data_dict)

        return await self._build_and_save_response(
            user_id, msg_id, req, response_text, intent, "completed", trace_id, structured_data=data_dict
        )

    async def _process_write_intent(
        self, user_id: uuid.UUID, intent: str, entities: Any, req: ConversationalRequest, 
        msg_id: uuid.UUID, trace_id: uuid.UUID,
        accounts, categories, credit_cards, goals, obligations,
        existing_resolved_data: dict | None = None,
        action_id: uuid.UUID | None = None,
        original_source_msg_id: uuid.UUID | None = None,
        extra_prefix_message: str | None = None,
        open_actions_to_cancel: list[PendingActionRead] | None = None
    ) -> ConversationalResponse:
        missing = {}
        resolved_data = existing_resolved_data.copy() if existing_resolved_data else {}
        ambiguous = []

        # Intent Promotion
        if intent == "create_expense" and getattr(entities, "credit_card", None):
            intent = "create_credit_card_purchase"

        try:
            # 1. Monto (común a casi todos)
            if intent in ["create_income", "create_expense", "create_goal_contribution", "create_obligation", "create_credit_card_purchase", "create_credit_card_payment"]:
                if entities.amount is not None:
                    resolved_data["amount"] = float(entities.amount)
                if "amount" not in resolved_data:
                    missing["amount"] = "¿Cuál fue el monto?"
            elif intent == "create_obligation_payment":
                if entities.amount is not None:
                    resolved_data["amount"] = float(entities.amount)
            
            # 2. Cuentas (común a ingresos, gastos, aportes, pagos TC)
            if intent in ["create_income", "create_expense", "create_goal_contribution", "create_credit_card_payment", "create_obligation_payment"]:
                if entities.account:
                    acc = resolve_entity(entities.account, accounts, lambda x: x.name, allow_missing=False)
                    resolved_data["account_id"] = str(acc.id)
                    resolved_data["_account_name"] = acc.name
                
                if "account_id" not in resolved_data:
                    if len(accounts) == 1:
                        resolved_data["account_id"] = str(accounts[0].id)
                        resolved_data["_account_name"] = accounts[0].name
                    else:
                        missing["account_id"] = "¿Desde qué cuenta hiciste el movimiento?" if intent != "create_income" else "¿A qué cuenta te ingresó?"
            
            # 3. Categorías (para ingresos y gastos)
            if intent in ["create_income", "create_expense"]:
                if entities.category:
                    try:
                        cat = resolve_entity(entities.category, categories, lambda x: x.name, allow_missing=True)
                        if cat:
                            resolved_data["category_id"] = str(cat.id)
                            resolved_data["_category_name"] = cat.name
                    except EntityNotFoundError:
                        pass
                
                if "category_id" not in resolved_data:
                    from app.conversations.entity_resolver import normalize_string
                    fallback = next((c for c in categories if normalize_string(c.name) == "sinclasificar" or normalize_string(c.name) == "sin clasificar"), None)
                    if fallback:
                        resolved_data["category_id"] = str(fallback.id)
                        resolved_data["_category_name"] = fallback.name

                if entities.description:
                    resolved_data["description"] = entities.description

            # 4. TC
            if intent in ["create_credit_card_purchase", "create_credit_card_payment"]:
                if entities.credit_card:
                    cc = resolve_entity(entities.credit_card, credit_cards, lambda x: x.name, allow_missing=False)
                    resolved_data["credit_card_id"] = str(cc.id)
                    resolved_data["_cc_name"] = cc.name
                if "credit_card_id" not in resolved_data:
                    missing["credit_card_id"] = "¿Con qué tarjeta hiciste la compra?" if intent == "create_credit_card_purchase" else "¿Qué tarjeta pagaste?"

                if intent == "create_credit_card_purchase":
                    if entities.installments_total:
                        resolved_data["installments_total"] = entities.installments_total
                    if "installments_total" not in resolved_data:
                        missing["installments_total"] = "¿A cuántas cuotas hiciste la compra?"

            # 5. Metas
            if intent == "create_goal_contribution":
                if entities.goal:
                    g = resolve_entity(entities.goal, goals, lambda x: x.name, allow_missing=False)
                    resolved_data["goal_id"] = str(g.id)
                    resolved_data["_goal_name"] = g.name
                if "goal_id" not in resolved_data:
                    missing["goal_id"] = "¿A qué meta quieres aportar?"
            elif intent == "create_goal":
                if entities.goal:
                    resolved_data["name"] = entities.goal
                if "name" not in resolved_data:
                    missing["name"] = "¿Cómo quieres llamar esta meta?"
                if entities.amount:
                    resolved_data["target_amount"] = float(entities.amount)
                if "target_amount" not in resolved_data:
                    missing["target_amount"] = "¿Cuál es el valor objetivo de la meta?"
                if entities.target_date:
                    resolved_data["target_date"] = entities.target_date

            # 6. Obligaciones
            if intent == "create_obligation":
                if entities.obligation:
                    resolved_data["name"] = entities.obligation
                if "name" not in resolved_data:
                    missing["name"] = "¿Cómo quieres llamar esta obligación?"
            elif intent == "create_obligation_payment":
                if entities.obligation:
                    ob = resolve_entity(entities.obligation, obligations, lambda x: x.name, allow_missing=False)
                    resolved_data["obligation_id"] = str(ob.id)
                    resolved_data["_obligation_name"] = ob.name
                    if "amount" not in resolved_data:
                        resolved_data["amount"] = float(ob.amount)
                if "obligation_id" not in resolved_data:
                    missing["obligation_id"] = "¿Qué obligación pagaste?"

        except AmbiguousEntityError as e:
            missing["ambiguity"] = e.args[0]
            names = [getattr(m, "name", str(m)) for m in e.matches]
            ambiguous.append(f"Encontré varias opciones para tu solicitud: {', '.join(names)}.")
        except EntityNotFoundError as e:
            missing["not_found"] = e.args[0]
            ambiguous.append(e.args[0])

        if missing or ambiguous:
            if open_actions_to_cancel:
                return await self._build_and_save_multiple_actions_response(user_id, msg_id, req, trace_id, open_actions_to_cancel)

            if action_id:
                await self.repo.update_pending_action(
                    action_id, data=resolved_data, missing_fields=missing, status="awaiting_clarification", command_id=None, intent=intent
                )
                action_ref_id = action_id
            else:
                action = await self.repo.create_pending_action(
                    user_id=user_id,
                    intent=intent,
                    data=resolved_data,
                    missing_fields=missing,
                    status="awaiting_clarification",
                    command_id=None,
                    source_message_id=msg_id
                )
                action_ref_id = action.id
            
            resp_text = " ".join(ambiguous) if ambiguous else list(missing.values())[0]
            if extra_prefix_message:
                resp_text = f"{extra_prefix_message} {resp_text}"
            return await self._build_and_save_response(
                user_id, msg_id, req, resp_text, intent, "awaiting_clarification", trace_id, pending_action_id=action_ref_id
            )
        
        # Todo claro -> pending_action awaiting_confirmation
        if open_actions_to_cancel:
            await self.repo.cancel_open_pending_actions(user_id)

        command_id = uuid.uuid4()
        if action_id:
            await self.repo.update_pending_action(
                action_id, data=resolved_data, missing_fields=None, status="awaiting_confirmation", command_id=command_id, intent=intent
            )
            action_ref_id = action_id
        else:
            action = await self.repo.create_pending_action(
                user_id=user_id,
                intent=intent,
                data=resolved_data,
                missing_fields=None,
                status="awaiting_confirmation",
                command_id=command_id,
                source_message_id=msg_id
            )
            action_ref_id = action.id

        resp_text = self._generate_confirmation_text(intent, resolved_data)
        if extra_prefix_message:
            resp_text = f"{extra_prefix_message} {resp_text}"

        return await self._build_and_save_response(
            user_id, msg_id, req, resp_text, intent, "awaiting_confirmation", trace_id, pending_action_id=action_ref_id
        )

    def _generate_confirmation_text(self, intent: str, data: dict) -> str:
        if intent == "create_expense":
            return f"Voy a registrar un gasto de ${data['amount']} en la cuenta {data.get('_account_name', 'default')}. ¿Confirmas?"
        elif intent == "create_income":
            return f"Voy a registrar un ingreso de ${data['amount']} en la cuenta {data.get('_account_name', 'default')}. ¿Confirmas?"
        elif intent == "create_credit_card_purchase":
            return f"Voy a registrar una compra por ${data['amount']} con la tarjeta {data['_cc_name']}. ¿Confirmas?"
        elif intent == "create_credit_card_payment":
            return f"Voy a registrar el pago de la tarjeta {data.get('_cc_name', '')} por ${data.get('amount')} desde la cuenta {data.get('_account_name', '')}. ¿Confirmas?"
        elif intent == "create_goal":
            return f"Voy a crear la meta '{data.get('name')}' por ${data.get('target_amount')}. ¿Confirmas?"
        elif intent == "create_goal_contribution":
            return f"Voy a aportar ${data.get('amount')} a la meta '{data.get('_goal_name', '')}' desde la cuenta {data.get('_account_name', '')}. ¿Confirmas?"
        elif intent == "create_obligation":
            return f"Voy a crear la obligación '{data.get('name')}' por ${data.get('amount')}. ¿Confirmas?"
        elif intent == "create_obligation_payment":
            return f"Voy a registrar el pago de la obligación '{data.get('_obligation_name', '')}' por ${data.get('amount')} desde la cuenta {data.get('_account_name', '')}. ¿Confirmas?"
        return "Voy a registrar esta operación. ¿Confirmas?"

    async def _handle_pending_action_flow(
        self, user_id: uuid.UUID, request: ConversationalRequest, trace_id: uuid.UUID, open_actions: list[PendingActionRead]
    ) -> ConversationalResponse:
        action = await self.repo.get_pending_action(request.pending_action_id)
        if not action or action.user_id != user_id:
            return ConversationalResponse(
                response_text="No encontré una acción pendiente válida.",
                intent="unknown",
                status="error",
                trace_id=trace_id
            )

        # Guardar mensaje inbound de confirmacion
        msg_id = await self.repo.save_message(
            user_id=user_id,
            channel=request.channel,
            direction="inbound",
            role="user",
            message_text=request.message,
            external_message_id=request.external_message_id,
            trace_id=trace_id
        )

        if action.status == "executed":
            return await self._build_and_save_response(
                user_id, msg_id, request, "Esta acción ya fue ejecutada previamente.",
                action.intent, "completed", trace_id
            )
        
        if action.status == "cancelled":
            return await self._build_and_save_response(
                user_id, msg_id, request, "Esta acción fue cancelada.",
                action.intent, "cancelled", trace_id
            )

        if action.status == "expired":
            return await self._build_and_save_response(
                user_id, msg_id, request, "Esta operación ha expirado. Por favor, comienza de nuevo.",
                action.intent, "error", trace_id
            )

        if action.status == "awaiting_clarification":
            # Extraer intent y entidades para la respuesta de clarificación
            accounts = await self.cash_service.account_repo.list_by_user(user_id)
            categories = await self.cash_service.category_repo.list_available(user_id)
            credit_cards = await self.credit_service.repo.get_all_for_user(user_id)
            goals = await self.goals_service.repository.list_active(user_id)
            obligations = await self.obl_service.repository.list_active(user_id)
            
            prompt = build_system_prompt(
                accounts=[a.name for a in accounts],
                categories=[c.name for c in categories],
                credit_cards=[cc.name for cc in credit_cards],
                goals=[g.name for g in goals],
                obligations=[o.name for o in obligations]
            )
            
            nlu_out, metadata = gemini_client.extract_intent_and_entities(
                message=request.message,
                system_prompt=prompt,
            )
            
            await self.repo.save_ai_run(
                user_id=user_id, message_id=msg_id, provider="gemini", model=metadata["model"],
                input_data={"message": request.message}, output_data=nlu_out.model_dump(mode="json"),
                intent=nlu_out.intent, success=metadata["success"], prompt_tokens=metadata["prompt_tokens"],
                completion_tokens=metadata["completion_tokens"], total_tokens=metadata["total_tokens"],
                latency_ms=metadata["latency_ms"], trace_id=trace_id
            )
            
            classification = classify_pending_reply(request.message)
            if classification == "cancel":
                await self.repo.update_pending_action_status(action.id, "cancelled")
                return await self._build_and_save_response(
                    user_id, msg_id, request, "Acción cancelada sin guardar.",
                    action.intent, "cancelled", trace_id
                )
            
            if is_write_intent(nlu_out.intent) and nlu_out.intent != action.intent:
                # El usuario cambió de intención claramente
                if len(open_actions) == 1:
                    await self.repo.update_pending_action_status(action.id, "cancelled")
                    return await self._process_write_intent(
                        user_id, nlu_out.intent, nlu_out.entities, request, msg_id, trace_id,
                        accounts, categories, credit_cards, goals, obligations,
                        extra_prefix_message="Entendido. Cancelé el registro anterior."
                    )
                else:
                    return await self._process_write_intent(
                        user_id, nlu_out.intent, nlu_out.entities, request, msg_id, trace_id,
                        accounts, categories, credit_cards, goals, obligations,
                        open_actions_to_cancel=open_actions
                    )
            
            # Si es intent="unknown" o el mismo, asumimos que es una clarificación. Hacemos merge.
            # En process_write_intent usamos el action_id para actualizar
            return await self._process_write_intent(
                user_id, action.intent, nlu_out.entities, request, msg_id, trace_id,
                accounts, categories, credit_cards, goals, obligations,
                existing_resolved_data=action.data,
                action_id=action.id,
                original_source_msg_id=action.source_message_id
            )

        classification = classify_pending_reply(request.message)

        if classification == "cancel":
            await self.repo.update_pending_action_status(action.id, "cancelled")
            return await self._build_and_save_response(
                user_id, msg_id, request, "Acción cancelada sin guardar.",
                action.intent, "cancelled", trace_id
            )
        
        if classification == "confirm":
            if action.status == "awaiting_confirmation":
                # Persistir confirmation_message_id ANTES de ejecutar
                await self.repo.update_pending_action_status(action.id, "awaiting_confirmation", confirmation_message_id=msg_id)
                try:
                    # Retrieve the original message for raw_message
                    raw_msg = None
                    if action.source_message_id:
                        original_msg_data = await self.repo.get_message_by_id(action.source_message_id)
                        if original_msg_data:
                            raw_msg = original_msg_data.get("message")
                    
                    # Ejecutar (se le pasa action pero también msg_id para la trazabilidad y raw_message)
                    await self._execute_financial_action(user_id, action, source_msg_id=action.source_message_id, raw_msg=raw_msg)
                    await self.repo.update_pending_action_status(action.id, "executed")
                    return await self._build_and_save_response(
                        user_id, msg_id, request, "¡Operación registrada con éxito!",
                        action.intent, "completed", trace_id
                    )
                except UnsupportedConversationalIntentError as e:
                    logger.warning(f"Fail-closed: {e}")
                    return await self._build_and_save_response(
                        user_id, msg_id, request,
                        "Todavía no puedo ejecutar esa operación automáticamente. No se registró ningún movimiento.",
                        action.intent, "error", trace_id
                    )
        
        # Unknown
        return await self._build_and_save_response(
            user_id, msg_id, request, "No entendí tu respuesta. Responde “sí” para confirmar o “no” para cancelar.",
            action.intent, "awaiting_confirmation", trace_id, pending_action_id=action.id
        )

    async def _execute_financial_action(
        self, user_id: uuid.UUID, action: PendingActionRead, source_msg_id: uuid.UUID | None = None, raw_msg: str | None = None
    ):
        intent = action.intent
        data = action.data
        command_id = action.command_id

        if intent == "create_expense":
            dto = CashExpenseCreate(
                amount=Decimal(str(data["amount"])),
                account_id=uuid.UUID(data["account_id"]),
                category_id=uuid.UUID(data["category_id"]) if "category_id" in data else None,
                description=data.get("description"),
                source_message_id=source_msg_id,
                raw_message=raw_msg,
            )
            await self.cash_service.create_expense(user_id, dto, command_id)
        elif intent == "create_income":
            dto = CashIncomeCreate(
                amount=Decimal(str(data["amount"])),
                account_id=uuid.UUID(data["account_id"]),
                category_id=uuid.UUID(data["category_id"]) if "category_id" in data else None,
                description=data.get("description"),
                source_message_id=source_msg_id,
                raw_message=raw_msg,
            )
            await self.cash_service.create_income(user_id, dto, command_id)
        elif intent == "create_credit_card_purchase":
            dto = CreditCardPurchaseCreate(
                amount=Decimal(str(data["amount"])),
                installments_total=data.get("installments_total", 1),
                source_message_id=source_msg_id,
                raw_message=raw_msg,
            )
            await self.credit_service.create_purchase(user_id, uuid.UUID(data["credit_card_id"]), dto, str(command_id))
        elif intent == "create_goal":
            dto = GoalCreate(
                name=data["name"],
                target_amount=Decimal(str(data["target_amount"])),
                target_date=data.get("target_date")
            )
            await self.goals_service.create_goal(user_id, dto)
        elif intent == "create_goal_contribution":
            dto = GoalContributionCreate(
                amount=Decimal(str(data["amount"])),
                account_id=uuid.UUID(data["account_id"]),
                source_message_id=source_msg_id,
                raw_message=raw_msg,
            )
            await self.goals_service.create_contribution(user_id, uuid.UUID(data["goal_id"]), dto, str(command_id))
        elif intent == "create_obligation":
            dto = ObligationCreate(
                name=data["name"],
                amount=Decimal(str(data["amount"])),
                due_day=data.get("due_day"),
                frequency=data.get("frequency"),
                category_id=uuid.UUID(data["category_id"]) if "category_id" in data else None
            )
            await self.obl_service.create_obligation(user_id, dto)
        elif intent == "create_obligation_payment":
            dto = ObligationPaymentCreate(
                amount=Decimal(str(data["amount"])),
                account_id=uuid.UUID(data["account_id"]),
                source_message_id=source_msg_id,
                raw_message=raw_msg,
            )
            await self.obl_service.create_payment(user_id, uuid.UUID(data["obligation_id"]), dto, str(command_id))
        elif intent == "create_credit_card_payment":
            dto = CreditCardPaymentCreate(
                amount=Decimal(str(data["amount"])),
                account_id=uuid.UUID(data["account_id"]),
                source_message_id=source_msg_id,
                raw_message=raw_msg,
            )
            await self.credit_service.create_payment(user_id, uuid.UUID(data["credit_card_id"]), dto, command_id)
        else:
            raise UnsupportedConversationalIntentError(f"Operación no soportada automáticamente: {intent}")

    async def _build_and_save_response(
        self, user_id: uuid.UUID, msg_id: uuid.UUID, req: ConversationalRequest, 
        response_text: str, intent: str, status: str, trace_id: uuid.UUID, 
        pending_action_id: uuid.UUID | None = None,
        structured_data: dict | None = None
    ) -> ConversationalResponse:
        
        resp = ConversationalResponse(
            response_text=response_text,
            intent=intent,
            status=status,
            trace_id=trace_id,
            pending_action_id=pending_action_id,
            structured_data=structured_data
        )

        await self.repo.save_message(
            user_id=user_id,
            channel=req.channel,
            direction="outbound",
            role="assistant",
            message_text=response_text,
            intent=intent,
            response_data=resp.model_dump(mode="json"),
            external_message_id=req.external_message_id,
            trace_id=trace_id
        )
        return resp

    async def _build_and_save_multiple_actions_response(
        self, user_id: uuid.UUID, msg_id: uuid.UUID, req: ConversationalRequest, trace_id: uuid.UUID, open_actions: list[PendingActionRead]
    ) -> ConversationalResponse:
        text_lines = ["Tienes varias operaciones pendientes:\n"]
        for i, act in enumerate(open_actions, 1):
            intent_name = act.intent
            amount = act.data.get("amount", "?")
            desc = ""
            if intent_name == "create_expense":
                desc = f"Gasto de {amount} COP"
            elif intent_name == "create_income":
                desc = f"Ingreso de {amount} COP"
            elif intent_name == "create_goal_contribution":
                desc = f"Aporte de {amount} COP a {act.data.get('_goal_name', 'Meta')}"
            elif intent_name == "create_obligation_payment":
                desc = f"Pago de obligación {act.data.get('_obligation_name', 'Obligación')}"
            elif intent_name == "create_credit_card_purchase":
                desc = f"Compra con tarjeta por {amount} COP"
            elif intent_name == "create_credit_card_payment":
                desc = f"Pago de tarjeta por {amount} COP"
            elif intent_name == "create_goal":
                desc = f"Creación de meta {act.data.get('name', 'N/A')}"
            elif intent_name == "create_obligation":
                desc = f"Creación de obligación {act.data.get('name', 'N/A')}"
            else:
                desc = f"Operación pendiente ({intent_name})"
            
            text_lines.append(f"{i}. {desc}")
        
        text_lines.append("\nResponde con el número de la operación que quieres continuar, o escribe \"cancelar todo\".")
        
        resp_text = "\n".join(text_lines)
        return await self._build_and_save_response(
            user_id, msg_id, req, resp_text, "unknown", "error", trace_id
        )
