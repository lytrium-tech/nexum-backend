-- Remove Indexes
DROP INDEX IF EXISTS idx_financial_events_source_msg;
DROP INDEX IF EXISTS idx_pending_actions_confirm_msg;
DROP INDEX IF EXISTS idx_pending_actions_source_msg;
DROP INDEX IF EXISTS idx_messages_trace_id;

-- Remove Foreign Keys
ALTER TABLE public.financial_events DROP CONSTRAINT IF EXISTS fk_fe_source_msg;
ALTER TABLE public.pending_actions DROP CONSTRAINT IF EXISTS fk_pa_confirm_msg;
ALTER TABLE public.pending_actions DROP CONSTRAINT IF EXISTS fk_pa_source_msg;

-- Remove Columns
ALTER TABLE public.financial_events DROP COLUMN IF EXISTS source_message_id;
ALTER TABLE public.pending_actions DROP COLUMN IF EXISTS confirmation_message_id;
ALTER TABLE public.pending_actions DROP COLUMN IF EXISTS source_message_id;
ALTER TABLE public.messages DROP COLUMN IF EXISTS trace_id;
