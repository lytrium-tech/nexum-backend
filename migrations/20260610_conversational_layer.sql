-- Migración para Fase 11: Conversational Layer MVP

-- 1. Modificar public.messages
ALTER TABLE public.messages
ADD COLUMN external_message_id TEXT NULL;

CREATE UNIQUE INDEX idx_messages_external_message_id_inbound
ON public.messages (channel, external_message_id)
WHERE external_message_id IS NOT NULL AND direction = 'inbound';

-- 2. Modificar public.pending_actions
ALTER TABLE public.pending_actions
ADD COLUMN command_id UUID NULL;

CREATE UNIQUE INDEX idx_pending_actions_command_id
ON public.pending_actions (command_id)
WHERE command_id IS NOT NULL;

-- 3. Actualizar constraint de status en pending_actions
ALTER TABLE public.pending_actions
DROP CONSTRAINT pending_actions_status_check;

ALTER TABLE public.pending_actions
ADD CONSTRAINT pending_actions_status_check
CHECK (status = ANY (ARRAY['awaiting_clarification'::text, 'awaiting_confirmation'::text, 'executed'::text, 'cancelled'::text, 'expired'::text, 'pending'::text, 'completed'::text, 'superseded'::text]));
