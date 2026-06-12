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
from app.credit.schemas import CreditCardPurchaseCreate
from app.credit.service import CreditCardService
from app.goals.service import GoalService
from app.integrations.gemini_client import gemini_client
from app.intelligence.service import IntelligenceService
from app.obligations.service import ObligationService

logger = logging.getLogger(__name__)

def classify_pending_reply(text: str) -> Literal["confirm", "cancel", "unknown"]:
    text_lower = text.lower().strip()
    text_lower = unicodedata.normalize('NFD', text_lower).encode('ascii', 'ignore').decode('utf-8')
    text_lower = re.sub(r'[^\w\s]', '', text_lower)
    text_lower = re.sub(r'\s+', ' ', text_lower).strip()

    confirm_phrases = {"si", "ok", "confirmo", "si confirmo", "si lo confirmo", "confirmar", "dale", "claro", "de acuerdo", "adelante", "hazlo", "si adelante", "si claro", "si hazlo"}
    cancel_phrases = {"no", "cancelar", "cancela", "olvidalo", "no confirmo", "mejor no", "no lo hagas", "detener", "no mejor no"}

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
        
        # 2. Verificar contexto de pending_action_id
        if request.pending_action_id:
            return await self._handle_pending_action_flow(user_id, request, trace_id)

        # 2.5 Fallback defensivo: Si no hay pending_action_id, pero el usuario dice algo que parece confirmacion/cancelacion
        # y hay exactamente una accion pendiente, la usamos.
        classification = classify_pending_reply(request.message)
        if classification in ["confirm", "cancel"]:
            latest_action = await self.repo.get_latest_open_pending_action(user_id)
            if latest_action:
                logger.info(f"Fallback defensivo aplicado: asociando respuesta a pending_action_id {latest_action.id}")
                request.pending_action_id = latest_action.id
                return await self._handle_pending_action_flow(user_id, request, trace_id)

        # 3. Guardar inbound message
        inbound_msg_id = await self.repo.save_message(
            user_id=user_id,
            channel=request.channel,
            direction="inbound",
            role="user",
            message_text=request.message,
            external_message_id=request.external_message_id
        )

        # 4. Traer contexto (Cuentas, Tarjetas, Metas, etc.) para el prompt
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

        # 5. Invocar Gemini
        nlu_out, metadata = gemini_client.extract_intent_and_entities(
            message=request.message,
            system_prompt=prompt,
        )

        # 6. Guardar AI Run
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

        # 7. Lógica por tipo de intento
        if is_read_intent(intent):
            return await self._process_read_intent(user_id, intent, request, inbound_msg_id, trace_id)

        if is_write_intent(intent):
            # Requiere resolución de entidades y crear pending action
            return await self._process_write_intent(
                user_id, intent, nlu_out.entities, request, inbound_msg_id, trace_id,
                accounts, categories, credit_cards, goals, obligations
            )
        
        # Default / Fallback
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
        accounts, categories, credit_cards, goals, obligations
    ) -> ConversationalResponse:
        
        missing = {}
        resolved_data = {}
        ambiguous = []

        try:
            # 1. Monto (común a casi todos)
            if intent in ["create_income", "create_expense", "create_goal_contribution", "create_obligation", "create_credit_card_purchase", "create_credit_card_payment"]:
                if entities.amount is not None:
                    resolved_data["amount"] = float(entities.amount)
                else:
                    missing["amount"] = "Falta el monto de la operación."
            
            # 2. Cuentas (común a ingresos, gastos, aportes, pagos TC)
            if intent in ["create_income", "create_expense", "create_goal_contribution", "create_credit_card_payment", "create_obligation_payment"]:
                if entities.account:
                    acc = resolve_entity(entities.account, accounts, lambda x: x.name, allow_missing=False)
                    resolved_data["account_id"] = str(acc.id)
                    resolved_data["_account_name"] = acc.name
                else:
                    if len(accounts) == 1:
                        resolved_data["account_id"] = str(accounts[0].id)
                        resolved_data["_account_name"] = accounts[0].name
                    else:
                        missing["account"] = "¿Desde qué cuenta o hacia qué cuenta?"
            
            # 3. Categorías (para ingresos y gastos)
            if intent in ["create_income", "create_expense"]:
                if entities.category:
                    cat = resolve_entity(entities.category, categories, lambda x: x.name, allow_missing=True)
                    if cat:
                        resolved_data["category_id"] = str(cat.id)
                        resolved_data["_category_name"] = cat.name
                if entities.description:
                    resolved_data["description"] = entities.description

            # 4. TC
            if intent in ["create_credit_card_purchase", "create_credit_card_payment"]:
                cc = resolve_entity(entities.credit_card, credit_cards, lambda x: x.name, allow_missing=False)
                resolved_data["credit_card_id"] = str(cc.id)
                resolved_data["_cc_name"] = cc.name

                if intent == "create_credit_card_purchase" and entities.installments_total:
                    resolved_data["installments_total"] = entities.installments_total

            # 5. Metas
            if intent == "create_goal_contribution":
                g = resolve_entity(entities.goal, goals, lambda x: x.name, allow_missing=False)
                resolved_data["goal_id"] = str(g.id)
                resolved_data["_goal_name"] = g.name
            
            # (El resto de mapeo se extiende siguiendo este patrón)

        except AmbiguousEntityError as e:
            missing["ambiguity"] = e.args[0]
            names = [getattr(m, "name", str(m)) for m in e.matches]
            ambiguous.append(f"Encontré varias opciones para tu solicitud: {', '.join(names)}.")
        except EntityNotFoundError as e:
            missing["not_found"] = e.args[0]
            ambiguous.append(e.args[0])

        if missing or ambiguous:
            # Crear pending_action awaiting_clarification
            action = await self.repo.create_pending_action(
                user_id=user_id,
                intent=intent,
                data=resolved_data,
                missing_fields=missing,
                status="awaiting_clarification",
                command_id=None
            )
            resp_text = " ".join(ambiguous) if ambiguous else "Me faltan algunos datos: " + ", ".join(missing.values())
            return await self._build_and_save_response(
                user_id, msg_id, req, resp_text, intent, "awaiting_clarification", trace_id, pending_action_id=action.id
            )
        
        # Todo claro -> pending_action awaiting_confirmation
        command_id = uuid.uuid4()
        action = await self.repo.create_pending_action(
            user_id=user_id,
            intent=intent,
            data=resolved_data,
            missing_fields=None,
            status="awaiting_confirmation",
            command_id=command_id
        )

        resp_text = self._generate_confirmation_text(intent, resolved_data)

        return await self._build_and_save_response(
            user_id, msg_id, req, resp_text, intent, "awaiting_confirmation", trace_id, pending_action_id=action.id
        )

    def _generate_confirmation_text(self, intent: str, data: dict) -> str:
        if intent == "create_expense":
            return f"Voy a registrar un gasto de ${data['amount']} en la cuenta {data.get('_account_name', 'default')}. ¿Confirmas?"
        elif intent == "create_income":
            return f"Voy a registrar un ingreso de ${data['amount']} en la cuenta {data.get('_account_name', 'default')}. ¿Confirmas?"
        elif intent == "create_credit_card_purchase":
            return f"Voy a registrar una compra por ${data['amount']} con la tarjeta {data['_cc_name']}. ¿Confirmas?"
        return "Voy a registrar esta operación. ¿Confirmas?"

    async def _handle_pending_action_flow(
        self, user_id: uuid.UUID, request: ConversationalRequest, trace_id: uuid.UUID
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
            external_message_id=request.external_message_id
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

        if action.status == "awaiting_clarification":
            # TODO: Fase Separada - Resolver clarificación interactiva fusionando entidades con action.data
            await self.repo.update_pending_action_status(action.id, "cancelled")
            return await self._build_and_save_response(
                user_id, msg_id, request, "La clarificación paso a paso aún está en desarrollo. Por favor, envía tu instrucción completa nuevamente (ej. 'Gasté 20.000 en Almuerzo usando Nequi').",
                action.intent, "cancelled", trace_id
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
                try:
                    # Ejecutar
                    await self._execute_financial_action(user_id, action)
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

    async def _execute_financial_action(self, user_id: uuid.UUID, action: PendingActionRead):
        intent = action.intent
        data = action.data
        command_id = action.command_id

        if intent == "create_expense":
            dto = CashExpenseCreate(
                amount=Decimal(str(data["amount"])),
                account_id=uuid.UUID(data["account_id"]),
                category_id=uuid.UUID(data["category_id"]) if "category_id" in data else None,
                description=data.get("description"),
            )
            await self.cash_service.create_expense(user_id, dto, command_id)
        elif intent == "create_income":
            dto = CashIncomeCreate(
                amount=Decimal(str(data["amount"])),
                account_id=uuid.UUID(data["account_id"]),
                category_id=uuid.UUID(data["category_id"]) if "category_id" in data else None,
                description=data.get("description"),
            )
            await self.cash_service.create_income(user_id, dto, command_id)
        elif intent == "create_credit_card_purchase":
            dto = CreditCardPurchaseCreate(
                amount=Decimal(str(data["amount"])),
                installments_total=data.get("installments_total", 1)
            )
            await self.credit_service.create_purchase(user_id, uuid.UUID(data["credit_card_id"]), dto, str(command_id))
        else:
            raise UnsupportedConversationalIntentError(f"Intent {intent} no soportado para ejecución automática.")
        # Add other intents...

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
            external_message_id=req.external_message_id
        )
        return resp
