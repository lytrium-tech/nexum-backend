-- Snapshot pre-Conversational Layer
-- Tablas: messages, pending_actions

CREATE TABLE IF NOT EXISTS public.messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    user_id uuid REFERENCES public.users(id) ON DELETE CASCADE,
    financial_event_id uuid REFERENCES public.financial_events(id) ON DELETE SET NULL,
    channel text DEFAULT 'n8n_chat'::text NOT NULL,
    direction text NOT NULL CHECK (direction = ANY (ARRAY['inbound'::text, 'outbound'::text])),
    role text NOT NULL CHECK (role = ANY (ARRAY['user'::text, 'assistant'::text, 'system'::text, 'tool'::text])),
    message text NOT NULL,
    intent text,
    parsed_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    response_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    model text,
    prompt_version text,
    source text DEFAULT 'n8n'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS public.pending_actions (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    user_id uuid REFERENCES public.users(id),
    intent text NOT NULL,
    data jsonb NOT NULL,
    missing_fields jsonb,
    status text DEFAULT 'pending'::text CHECK (status = ANY (ARRAY['pending'::text, 'completed'::text, 'cancelled'::text, 'expired'::text, 'superseded'::text])),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    expires_at timestamp with time zone,
    metadata jsonb DEFAULT '{}'::jsonb
);
