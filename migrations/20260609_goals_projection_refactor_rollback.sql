BEGIN;

DROP VIEW IF EXISTS public.v_financial_snapshot_current_month CASCADE;
DROP VIEW IF EXISTS public.v_goals_current_month CASCADE;

ALTER TABLE public.goals 
  ADD COLUMN IF NOT EXISTS progress_percentage numeric(5,2) DEFAULT 0.00,
  ADD COLUMN IF NOT EXISTS monthly_required numeric(14,2),
  ADD COLUMN IF NOT EXISTS daily_required numeric(14,2);

CREATE OR REPLACE FUNCTION public.recalculate_goal_fields()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
declare
  v_remaining numeric(14,2);
  v_days_left int;
  v_months_left numeric;
  v_today date;
begin
  v_today := (now() at time zone 'America/Bogota')::date;

  new.current_amount := coalesce(new.current_amount, 0);

  if new.target_amount is null or new.target_amount <= 0 then
    new.progress_percentage := 0;
    new.monthly_required := null;
    new.daily_required := null;
    return new;
  end if;

  if new.current_amount >= new.target_amount then
    new.current_amount := new.target_amount;
    new.progress_percentage := 100;
    new.monthly_required := 0;
    new.daily_required := 0;
    new.status := 'completed';
    return new;
  end if;

  v_remaining := new.target_amount - new.current_amount;

  new.progress_percentage := round(((new.current_amount / new.target_amount) * 100)::numeric, 2);

  if new.target_date is null then
    new.monthly_required := null;
    new.daily_required := null;
    return new;
  end if;

  v_days_left := new.target_date - v_today;

  if v_days_left <= 0 then
    new.monthly_required := 0;
    new.daily_required := 0;
    return new;
  end if;

  v_months_left := greatest(v_days_left::numeric / 30, 1);

  new.monthly_required := round((v_remaining / v_months_left)::numeric, 2);
  new.daily_required := round((v_remaining / v_days_left)::numeric, 2);

  return new;
end;
$function$;

CREATE TRIGGER trg_goals_recalculate BEFORE INSERT OR UPDATE OF target_amount, current_amount, target_date, status ON public.goals FOR EACH ROW EXECUTE FUNCTION recalculate_goal_fields();

CREATE OR REPLACE VIEW public.v_goals_current_month AS
 WITH current_period AS (
         SELECT to_char((now() AT TIME ZONE 'America/Bogota'::text), 'YYYY-MM'::text) AS period
        ), goal_contributions_month AS (
         SELECT gc.user_id,
            gc.goal_id,
            gc.period,
            sum(gc.amount) AS contributed_this_period
           FROM goal_contributions gc
          GROUP BY gc.user_id, gc.goal_id, gc.period
        )
 SELECT g.user_id,
    g.id AS goal_id,
    g.name,
    g.target_amount,
    g.current_amount,
    g.target_date,
    g.monthly_required,
    g.daily_required,
    g.progress_percentage,
    g.status,
    cp.period,
    COALESCE(gcm.contributed_this_period, (0)::numeric) AS contributed_this_period,
    GREATEST((COALESCE(g.monthly_required, (0)::numeric) - COALESCE(gcm.contributed_this_period, (0)::numeric)), (0)::numeric) AS remaining_required_this_period
   FROM ((goals g
     CROSS JOIN current_period cp)
     LEFT JOIN goal_contributions_month gcm ON (((gcm.goal_id = g.id) AND (gcm.user_id = g.user_id) AND (gcm.period = cp.period))))
  WHERE ((g.is_active = true) AND (g.status = ANY (ARRAY['active'::text, 'paused'::text])));

CREATE OR REPLACE VIEW public.v_financial_snapshot_current_month AS
 WITH account_totals AS (
         SELECT accounts.user_id,
            sum(accounts.balance) AS available_real
           FROM accounts
          WHERE (accounts.is_active = true)
          GROUP BY accounts.user_id
        ), card_totals AS (
         SELECT v_credit_card_debt.user_id,
            sum(v_credit_card_debt.credit_card_debt) AS credit_card_debt,
            sum(v_credit_card_debt.monthly_cc_payment) AS monthly_cc_payment
           FROM v_credit_card_debt
          GROUP BY v_credit_card_debt.user_id
        ), obligation_totals AS (
         SELECT v_pending_obligations_current_month.user_id,
            sum(
                CASE
                    WHEN (v_pending_obligations_current_month.is_pending = true) THEN v_pending_obligations_current_month.amount
                    ELSE (0)::numeric
                END) AS pending_obligations_total
           FROM v_pending_obligations_current_month
          GROUP BY v_pending_obligations_current_month.user_id
        ), goal_totals AS (
         SELECT v_goals_current_month.user_id,
            sum(v_goals_current_month.remaining_required_this_period) AS monthly_goals_required_remaining
           FROM v_goals_current_month
          GROUP BY v_goals_current_month.user_id
        )
 SELECT u.id AS user_id,
    u.name AS user_name,
    COALESCE(at.available_real, (0)::numeric) AS available_real,
    COALESCE(ct.credit_card_debt, (0)::numeric) AS credit_card_debt,
    COALESCE(ct.monthly_cc_payment, (0)::numeric) AS monthly_cc_payment,
    COALESCE(ot.pending_obligations_total, (0)::numeric) AS pending_obligations_total,
    COALESCE(gt.monthly_goals_required_remaining, (0)::numeric) AS monthly_goals_required_remaining,
    ((COALESCE(at.available_real, (0)::numeric) - COALESCE(ct.monthly_cc_payment, (0)::numeric)) - COALESCE(ot.pending_obligations_total, (0)::numeric)) AS safe_money,
    (((COALESCE(at.available_real, (0)::numeric) - COALESCE(ct.monthly_cc_payment, (0)::numeric)) - COALESCE(ot.pending_obligations_total, (0)::numeric)) - COALESCE(gt.monthly_goals_required_remaining, (0)::numeric)) AS free_money,
    to_char((now() AT TIME ZONE 'America/Bogota'::text), 'YYYY-MM'::text) AS period,
    now() AS calculated_at
   FROM ((((users u
     LEFT JOIN account_totals at ON ((at.user_id = u.id)))
     LEFT JOIN card_totals ct ON ((ct.user_id = u.id)))
     LEFT JOIN obligation_totals ot ON ((ot.user_id = u.id)))
     LEFT JOIN goal_totals gt ON ((gt.user_id = u.id)))
  WHERE (u.status = 'active'::text);

-- Trigger recalculation on all goals to fill the columns back
UPDATE public.goals SET updated_at = now();

COMMIT;
