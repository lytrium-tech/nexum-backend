-- ==============================================================================
-- Nexum Phase B.0: Legacy Transactions Removal
-- ==============================================================================
-- Retires the deprecated public.transactions table created during the n8n era.
-- The official ledger is now public.financial_events.
--
-- Rollback strategy:
-- ALTER TABLE public.transactions_legacy_backup RENAME TO transactions;
-- ==============================================================================

ALTER TABLE public.transactions RENAME TO transactions_legacy_backup;

COMMENT ON TABLE public.transactions_legacy_backup IS 
'Legacy backup of public.transactions before retirement. Official ledger is public.financial_events.';
