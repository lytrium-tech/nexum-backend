-- WARNING: This schema is for context only and is not meant to be run.
-- Table order and constraints may not be valid for execution.

CREATE TABLE public.accounts (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid,
  name text,
  type text CHECK (type = ANY (ARRAY['bank'::text, 'wallet'::text, 'cash'::text, 'savings'::text])),
  balance numeric DEFAULT 0 CHECK (balance >= 0::numeric),
  created_at timestamp without time zone DEFAULT now(),
  currency text NOT NULL DEFAULT 'COP'::text,
  is_active boolean NOT NULL DEFAULT true,
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT accounts_pkey PRIMARY KEY (id),
  CONSTRAINT accounts_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id)
);

CREATE TABLE public.ai_runs (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid,
  message_id uuid,
  financial_event_id uuid,
  provider text NOT NULL DEFAULT 'gemini'::text CHECK (provider = ANY (ARRAY['openai'::text, 'gemini'::text, 'anthropic'::text, 'local'::text, 'other'::text])),
  model text,
  prompt_version text,
  input jsonb NOT NULL DEFAULT '{}'::jsonb,
  output jsonb NOT NULL DEFAULT '{}'::jsonb,
  intent text,
  success boolean NOT NULL DEFAULT true,
  error_message text,
  prompt_tokens integer,
  completion_tokens integer,
  total_tokens integer,
  latency_ms integer CHECK (latency_ms IS NULL OR latency_ms >= 0),
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  trace_id uuid,
  command_id uuid,
  parse_ok boolean,
  redacted_payload jsonb DEFAULT '{}'::jsonb,
  error_metadata jsonb DEFAULT '{}'::jsonb,
  CONSTRAINT ai_runs_pkey PRIMARY KEY (id),
  CONSTRAINT ai_runs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id),
  CONSTRAINT ai_runs_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id),
  CONSTRAINT ai_runs_financial_event_id_fkey FOREIGN KEY (financial_event_id) REFERENCES public.financial_events(id)
);

CREATE TABLE public.categories (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid,
  name text NOT NULL,
  type text CHECK (type = ANY (ARRAY['income'::text, 'expense'::text, 'credit_card'::text, 'obligation'::text, 'goal'::text, 'transfer'::text, 'system'::text])),
  is_active boolean DEFAULT true,
  created_at timestamp without time zone DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT categories_pkey PRIMARY KEY (id),
  CONSTRAINT categories_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id)
);

CREATE TABLE public.credit_card_transactions (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid,
  credit_card_id uuid,
  type text CHECK (type = ANY (ARRAY['purchase'::text, 'payment'::text, 'fee'::text, 'interest'::text, 'adjustment'::text])),
  amount numeric CHECK (amount > 0::numeric),
  category text,
  description text,
  created_at timestamp without time zone DEFAULT now(),
  installments_total integer DEFAULT 1,
  installments_paid integer DEFAULT 0,
  monthly_amount numeric,
  interest_amount numeric DEFAULT 0,
  total_with_interest numeric,
  event_id uuid,
  account_id uuid,
  category_id uuid,
  occurred_at timestamp with time zone NOT NULL DEFAULT now(),
  period text,
  source text NOT NULL DEFAULT 'n8n_chat'::text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT credit_card_transactions_pkey PRIMARY KEY (id),
  CONSTRAINT credit_card_transactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id),
  CONSTRAINT credit_card_transactions_credit_card_id_fkey FOREIGN KEY (credit_card_id) REFERENCES public.credit_cards(id),
  CONSTRAINT credit_card_transactions_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.financial_events(id),
  CONSTRAINT credit_card_transactions_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id),
  CONSTRAINT credit_card_transactions_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.categories(id)
);

CREATE TABLE public.credit_cards (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid,
  name text,
  bank text,
  credit_limit numeric,
  current_debt numeric DEFAULT 0,
  cutoff_day integer CHECK (cutoff_day IS NULL OR cutoff_day >= 1 AND cutoff_day <= 31),
  due_day integer CHECK (due_day IS NULL OR due_day >= 1 AND due_day <= 31),
  management_fee numeric DEFAULT 0,
  created_at timestamp without time zone DEFAULT now(),
  monthly_interest_rate numeric DEFAULT 0,
  annual_interest_rate numeric DEFAULT 0,
  currency text NOT NULL DEFAULT 'COP'::text,
  is_active boolean NOT NULL DEFAULT true,
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT credit_cards_pkey PRIMARY KEY (id),
  CONSTRAINT credit_cards_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id)
);

CREATE TABLE public.financial_events (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  account_id uuid,
  category_id uuid,
  event_type text NOT NULL CHECK (event_type = ANY (ARRAY['income'::text, 'expense'::text, 'credit_card_purchase'::text, 'credit_card_payment'::text, 'obligation_payment'::text, 'goal_contribution'::text, 'manual_adjustment'::text])),
  direction text NOT NULL CHECK (direction = ANY (ARRAY['inflow'::text, 'outflow'::text, 'neutral'::text])),
  amount numeric NOT NULL CHECK (amount > 0::numeric),
  currency text NOT NULL DEFAULT 'COP'::text,
  description text,
  raw_message text,
  source text NOT NULL DEFAULT 'n8n_chat'::text,
  occurred_at timestamp with time zone NOT NULL DEFAULT now(),
  period text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  command_id uuid,
  CONSTRAINT financial_events_pkey PRIMARY KEY (id),
  CONSTRAINT financial_events_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id),
  CONSTRAINT financial_events_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id),
  CONSTRAINT financial_events_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.categories(id)
);

CREATE TABLE public.goal_contributions (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  goal_id uuid NOT NULL,
  event_id uuid,
  account_id uuid,
  amount numeric NOT NULL CHECK (amount > 0::numeric),
  period text,
  contributed_at timestamp with time zone NOT NULL DEFAULT now(),
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT goal_contributions_pkey PRIMARY KEY (id),
  CONSTRAINT goal_contributions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id),
  CONSTRAINT goal_contributions_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES public.goals(id),
  CONSTRAINT goal_contributions_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.financial_events(id),
  CONSTRAINT goal_contributions_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id)
);

CREATE TABLE public.goals (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid,
  name text,
  target_amount numeric CHECK (target_amount IS NULL OR target_amount > 0::numeric),
  current_amount numeric DEFAULT 0 CHECK (current_amount >= 0::numeric),
  created_at timestamp without time zone DEFAULT now(),
  target_date date,
  monthly_required numeric,
  daily_required numeric,
  progress_percentage numeric CHECK (progress_percentage >= 0::numeric AND progress_percentage <= 100::numeric),
  currency text NOT NULL DEFAULT 'COP'::text,
  is_active boolean NOT NULL DEFAULT true,
  status text NOT NULL DEFAULT 'active'::text CHECK (status = ANY (ARRAY['active'::text, 'completed'::text, 'paused'::text, 'cancelled'::text])),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT goals_pkey PRIMARY KEY (id),
  CONSTRAINT goals_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id)
);

CREATE TABLE public.idempotency_keys (
  idempotency_key text NOT NULL,
  command_id uuid NOT NULL,
  source text NOT NULL,
  status text NOT NULL DEFAULT 'processing'::text CHECK (status = ANY (ARRAY['processing'::text, 'completed'::text, 'failed'::text])),
  response_payload jsonb,
  error_metadata jsonb,
  created_at timestamp with time zone DEFAULT now(),
  updated_at timestamp with time zone DEFAULT now(),
  CONSTRAINT idempotency_keys_pkey PRIMARY KEY (idempotency_key)
);

CREATE TABLE public.logs (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid,
  raw_message text,
  intent text,
  extracted_data jsonb,
  ai_response text,
  created_at timestamp without time zone DEFAULT now(),
  message_id uuid,
  ai_run_id uuid,
  financial_event_id uuid,
  level text NOT NULL DEFAULT 'info'::text CHECK (level = ANY (ARRAY['debug'::text, 'info'::text, 'warning'::text, 'error'::text])),
  source text NOT NULL DEFAULT 'n8n'::text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  trace_id uuid,
  command_id uuid,
  idempotency_key text,
  channel text,
  workflow text,
  domain text,
  response_code text,
  status text,
  latency_ms integer,
  parse_ok boolean,
  redacted_payload jsonb DEFAULT '{}'::jsonb,
  error_metadata jsonb DEFAULT '{}'::jsonb,
  CONSTRAINT logs_pkey PRIMARY KEY (id),
  CONSTRAINT logs_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id),
  CONSTRAINT logs_ai_run_id_fkey FOREIGN KEY (ai_run_id) REFERENCES public.ai_runs(id),
  CONSTRAINT logs_financial_event_id_fkey FOREIGN KEY (financial_event_id) REFERENCES public.financial_events(id)
);

CREATE TABLE public.messages (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid,
  financial_event_id uuid,
  channel text NOT NULL DEFAULT 'n8n_chat'::text,
  direction text NOT NULL CHECK (direction = ANY (ARRAY['inbound'::text, 'outbound'::text])),
  role text NOT NULL CHECK (role = ANY (ARRAY['user'::text, 'assistant'::text, 'system'::text, 'tool'::text])),
  message text NOT NULL,
  intent text,
  parsed_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  response_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  model text,
  prompt_version text,
  source text NOT NULL DEFAULT 'n8n'::text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT messages_pkey PRIMARY KEY (id),
  CONSTRAINT messages_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id),
  CONSTRAINT messages_financial_event_id_fkey FOREIGN KEY (financial_event_id) REFERENCES public.financial_events(id)
);

CREATE TABLE public.obligation_payments (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid,
  obligation_id uuid,
  amount numeric NOT NULL CHECK (amount > 0::numeric),
  paid_at timestamp without time zone DEFAULT now(),
  period text NOT NULL,
  account_id uuid,
  created_at timestamp without time zone DEFAULT now(),
  event_id uuid,
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT obligation_payments_pkey PRIMARY KEY (id),
  CONSTRAINT obligation_payments_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id),
  CONSTRAINT obligation_payments_obligation_id_fkey FOREIGN KEY (obligation_id) REFERENCES public.obligations(id),
  CONSTRAINT obligation_payments_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id),
  CONSTRAINT obligation_payments_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.financial_events(id)
);

CREATE TABLE public.obligations (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid,
  name text,
  amount numeric CHECK (amount > 0::numeric),
  due_day integer CHECK (due_day IS NULL OR due_day >= 1 AND due_day <= 31),
  frequency text CHECK (frequency = ANY (ARRAY['weekly'::text, 'monthly'::text, 'yearly'::text, 'once'::text])),
  is_active boolean DEFAULT true,
  created_at timestamp without time zone DEFAULT now(),
  category_id uuid,
  currency text NOT NULL DEFAULT 'COP'::text,
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT obligations_pkey PRIMARY KEY (id),
  CONSTRAINT obligations_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id),
  CONSTRAINT obligations_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.categories(id)
);

CREATE TABLE public.pending_actions (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid,
  intent text NOT NULL,
  data jsonb NOT NULL,
  missing_fields jsonb,
  status text DEFAULT 'pending'::text CHECK (status = ANY (ARRAY['pending'::text, 'completed'::text, 'cancelled'::text, 'expired'::text, 'superseded'::text])),
  created_at timestamp without time zone DEFAULT now(),
  updated_at timestamp without time zone DEFAULT now(),
  expires_at timestamp with time zone,
  metadata jsonb DEFAULT '{}'::jsonb,
  CONSTRAINT pending_actions_pkey PRIMARY KEY (id),
  CONSTRAINT pending_actions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id)
);

-- DEPRECATED / LEGACY TABLE
-- This table is maintained for backward compatibility only.
-- Nexum now uses 'public.financial_events' as the universal ledger for all operations.
-- The n8n workflow ('Nexum by Lytrium') no longer writes to this table.
-- DO NOT use this table for new logic or features.
-- Future removal requires confirming that no Frontend (React) views or database views depend on this data.
CREATE TABLE public.transactions (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid,
  account_id uuid,
  type text CHECK (type = ANY (ARRAY['income'::text, 'expense'::text])),
  amount numeric CHECK (amount > 0::numeric),
  category text,
  description text,
  transaction_date timestamp without time zone DEFAULT now(),
  created_at timestamp without time zone DEFAULT now(),
  event_id uuid,
  category_id uuid,
  occurred_at timestamp with time zone DEFAULT now(),
  period text,
  source text DEFAULT 'n8n_chat'::text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT transactions_pkey PRIMARY KEY (id),
  CONSTRAINT transactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id),
  CONSTRAINT transactions_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id),
  CONSTRAINT transactions_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.financial_events(id),
  CONSTRAINT transactions_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.categories(id)
);

CREATE TABLE public.user_channels (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  channel text NOT NULL CHECK (channel = ANY (ARRAY['whatsapp'::text, 'n8n_chat'::text, 'web'::text, 'email'::text, 'telegram'::text])),
  external_id text NOT NULL,
  display_name text,
  is_primary boolean NOT NULL DEFAULT false,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT user_channels_pkey PRIMARY KEY (id),
  CONSTRAINT user_channels_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id)
);

CREATE TABLE public.users (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  name text,
  phone text,
  created_at timestamp without time zone DEFAULT now(),
  auth_user_id uuid UNIQUE,
  email text UNIQUE,
  timezone text NOT NULL DEFAULT 'America/Bogota'::text,
  currency text NOT NULL DEFAULT 'COP'::text CHECK (currency = ANY (ARRAY['COP'::text, 'USD'::text])),
  status text NOT NULL DEFAULT 'active'::text CHECK (status = ANY (ARRAY['active'::text, 'inactive'::text, 'blocked'::text])),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT users_pkey PRIMARY KEY (id)
);
