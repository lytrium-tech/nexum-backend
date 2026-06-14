BEGIN;

-- 1. Modificar el Check Constraint de financial_events para aceptar transfer_in y transfer_out
ALTER TABLE public.financial_events DROP CONSTRAINT IF EXISTS financial_events_type_check;

ALTER TABLE public.financial_events ADD CONSTRAINT financial_events_type_check 
CHECK (event_type = ANY (ARRAY['income', 'expense', 'credit_card_purchase', 'credit_card_payment', 'obligation_payment', 'goal_contribution', 'manual_adjustment', 'transfer_out', 'transfer_in']));

-- 2. Crear tabla transfers
CREATE TABLE IF NOT EXISTS public.transfers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    source_account_id UUID NOT NULL REFERENCES public.accounts(id) ON DELETE RESTRICT,
    destination_account_id UUID NOT NULL REFERENCES public.accounts(id) ON DELETE RESTRICT,
    amount NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL DEFAULT 'COP',
    description TEXT,
    command_id UUID UNIQUE,
    source_message_id UUID,
    raw_message TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

-- Constraints adicionales para transfers
ALTER TABLE public.transfers DROP CONSTRAINT IF EXISTS check_different_accounts;
ALTER TABLE public.transfers ADD CONSTRAINT check_different_accounts CHECK (source_account_id != destination_account_id);

-- Índices
CREATE INDEX IF NOT EXISTS idx_transfers_user_id ON public.transfers(user_id);

-- 3. Modificar financial_events para incluir transfer_id
ALTER TABLE public.financial_events ADD COLUMN IF NOT EXISTS transfer_id UUID REFERENCES public.transfers(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_financial_events_transfer_id ON public.financial_events(transfer_id);

COMMIT;
