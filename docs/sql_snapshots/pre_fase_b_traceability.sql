-- Traceability for messages
ALTER TABLE public.messages
ADD COLUMN IF NOT EXISTS trace_id UUID NULL;

-- Traceability for conversational intents
ALTER TABLE public.pending_actions
ADD COLUMN IF NOT EXISTS source_message_id UUID NULL,
ADD COLUMN IF NOT EXISTS confirmation_message_id UUID NULL;

-- Traceability for financial events
ALTER TABLE public.financial_events
ADD COLUMN IF NOT EXISTS source_message_id UUID NULL;

-- Foreign Keys
ALTER TABLE public.pending_actions
DROP CONSTRAINT IF EXISTS fk_pa_source_msg;
ALTER TABLE public.pending_actions
ADD CONSTRAINT fk_pa_source_msg FOREIGN KEY (source_message_id) REFERENCES public.messages(id) ON DELETE SET NULL;

ALTER TABLE public.pending_actions
DROP CONSTRAINT IF EXISTS fk_pa_confirm_msg;
ALTER TABLE public.pending_actions
ADD CONSTRAINT fk_pa_confirm_msg FOREIGN KEY (confirmation_message_id) REFERENCES public.messages(id) ON DELETE SET NULL;

ALTER TABLE public.financial_events
DROP CONSTRAINT IF EXISTS fk_fe_source_msg;
ALTER TABLE public.financial_events
ADD CONSTRAINT fk_fe_source_msg FOREIGN KEY (source_message_id) REFERENCES public.messages(id) ON DELETE SET NULL;

-- Indexes for performance on tracing
CREATE INDEX IF NOT EXISTS idx_messages_trace_id ON public.messages(trace_id);
CREATE INDEX IF NOT EXISTS idx_pending_actions_source_msg ON public.pending_actions(source_message_id);
CREATE INDEX IF NOT EXISTS idx_pending_actions_confirm_msg ON public.pending_actions(confirmation_message_id);
CREATE INDEX IF NOT EXISTS idx_financial_events_source_msg ON public.financial_events(source_message_id);
