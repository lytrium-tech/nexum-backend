BEGIN;

ALTER TABLE public.transfers ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS transfers_select_own ON public.transfers;
CREATE POLICY transfers_select_own ON public.transfers
    FOR SELECT TO authenticated
    USING (user_id = current_app_user_id());

DROP POLICY IF EXISTS transfers_insert_own ON public.transfers;
CREATE POLICY transfers_insert_own ON public.transfers
    FOR INSERT TO authenticated
    WITH CHECK (user_id = current_app_user_id());

DROP POLICY IF EXISTS transfers_update_own ON public.transfers;
CREATE POLICY transfers_update_own ON public.transfers
    FOR UPDATE TO authenticated
    USING (user_id = current_app_user_id())
    WITH CHECK (user_id = current_app_user_id());

DROP POLICY IF EXISTS transfers_delete_own ON public.transfers;
CREATE POLICY transfers_delete_own ON public.transfers
    FOR DELETE TO authenticated
    USING (user_id = current_app_user_id());

COMMIT;
