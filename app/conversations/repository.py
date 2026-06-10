import uuid
from typing import Any

# Usaremos consultas crudas o tabla mapeada para pending_actions, messages, ai_runs.
# Para mantenerlo simple, usaremos sqlalchemy core.
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.schemas import PendingActionRead


class ConversationsRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_message_by_external_id(self, channel: str, external_message_id: str) -> dict[str, Any] | None:
        """
        Retorna el mensaje (si existe) para deduplicar.
        """
        query = text("""
            SELECT id, response_data
            FROM public.messages
            WHERE channel = :channel
              AND external_message_id = :external_message_id
              AND direction = 'outbound'
            LIMIT 1
        """)
        result = await self.session.execute(query, {"channel": channel, "external_message_id": external_message_id})
        row = result.mappings().first()
        if row:
            return dict(row)
        return None

    async def save_message(
        self,
        user_id: uuid.UUID,
        channel: str,
        direction: str,
        role: str,
        message_text: str,
        intent: str | None = None,
        parsed_data: dict | None = None,
        response_data: dict | None = None,
        external_message_id: str | None = None,
    ) -> uuid.UUID:
        
        query = text("""
            INSERT INTO public.messages (
                user_id, channel, direction, role, message, intent, 
                parsed_data, response_data, external_message_id
            ) VALUES (
                :user_id, :channel, :direction, :role, :message, :intent,
                :parsed_data, :response_data, :external_message_id
            ) RETURNING id
        """)
        
        import json
        params = {
            "user_id": user_id,
            "channel": channel,
            "direction": direction,
            "role": role,
            "message": message_text,
            "intent": intent,
            "parsed_data": json.dumps(parsed_data or {}),
            "response_data": json.dumps(response_data or {}),
            "external_message_id": external_message_id
        }
        
        result = await self.session.execute(query, params)
        row = result.fetchone()
        return row[0] if row else uuid.uuid4()

    async def save_ai_run(
        self,
        user_id: uuid.UUID,
        message_id: uuid.UUID,
        provider: str,
        model: str,
        input_data: dict,
        output_data: dict,
        intent: str | None,
        success: bool,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        latency_ms: int,
        trace_id: uuid.UUID
    ) -> uuid.UUID:
        
        import json
        
        query = text("""
            INSERT INTO public.ai_runs (
                user_id, message_id, provider, model, input, output,
                intent, success, prompt_tokens, completion_tokens,
                total_tokens, latency_ms, trace_id
            ) VALUES (
                :user_id, :message_id, :provider, :model, :input, :output,
                :intent, :success, :prompt_tokens, :completion_tokens,
                :total_tokens, :latency_ms, :trace_id
            ) RETURNING id
        """)
        
        params = {
            "user_id": user_id,
            "message_id": message_id,
            "provider": provider,
            "model": model,
            "input": json.dumps(input_data),
            "output": json.dumps(output_data),
            "intent": intent,
            "success": success,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "latency_ms": latency_ms,
            "trace_id": trace_id
        }
        
        result = await self.session.execute(query, params)
        row = result.fetchone()
        return row[0] if row else uuid.uuid4()


    async def create_pending_action(
        self,
        user_id: uuid.UUID,
        intent: str,
        data: dict,
        missing_fields: dict | None,
        status: str,
        command_id: uuid.UUID | None
    ) -> PendingActionRead:
        import json
        query = text("""
            INSERT INTO public.pending_actions (
                user_id, intent, data, missing_fields, status, command_id
            ) VALUES (
                :user_id, :intent, :data, :missing_fields, :status, :command_id
            ) RETURNING id, user_id, intent, data, missing_fields, status, command_id, created_at, updated_at, expires_at
        """)
        
        params = {
            "user_id": user_id,
            "intent": intent,
            "data": json.dumps(data),
            "missing_fields": json.dumps(missing_fields) if missing_fields else None,
            "status": status,
            "command_id": command_id
        }
        
        result = await self.session.execute(query, params)
        row = result.mappings().first()
        return PendingActionRead(**dict(row))

    async def get_pending_action(self, action_id: uuid.UUID) -> PendingActionRead | None:
        query = text("""
            SELECT id, user_id, intent, data, missing_fields, status, command_id, created_at, updated_at, expires_at
            FROM public.pending_actions
            WHERE id = :id
        """)
        result = await self.session.execute(query, {"id": action_id})
        row = result.mappings().first()
        if row:
            return PendingActionRead(**dict(row))
        return None

    async def update_pending_action_status(self, action_id: uuid.UUID, status: str) -> None:
        query = text("""
            UPDATE public.pending_actions
            SET status = :status, updated_at = now()
            WHERE id = :id
        """)
        await self.session.execute(query, {"id": action_id, "status": status})
