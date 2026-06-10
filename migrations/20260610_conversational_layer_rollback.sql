-- Rollback de Migración para Fase 11: Conversational Layer MVP

-- 1. Revertir constraint de status en pending_actions
ALTER TABLE public.pending_actions
DROP CONSTRAINT pending_actions_status_check;

ALTER TABLE public.pending_actions
ADD CONSTRAINT pending_actions_status_check
CHECK (status = ANY (ARRAY['pending'::text, 'completed'::text, 'cancelled'::text, 'expired'::text, 'superseded'::text]));

-- 2. Revertir public.pending_actions
DROP INDEX IF EXISTS idx_pending_actions_command_id;
ALTER TABLE public.pending_actions
DROP COLUMN command_id;

-- 3. Revertir public.messages
DROP INDEX IF EXISTS idx_messages_external_message_id_inbound;
ALTER TABLE public.messages
DROP COLUMN external_message_id;
