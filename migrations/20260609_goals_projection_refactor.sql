BEGIN;

DROP VIEW IF EXISTS public.v_financial_snapshot_current_month CASCADE;
DROP VIEW IF EXISTS public.v_goals_current_month CASCADE;
DROP TRIGGER IF EXISTS trg_goals_recalculate ON public.goals;
DROP FUNCTION IF EXISTS public.recalculate_goal_fields();

ALTER TABLE public.goals 
  DROP COLUMN IF EXISTS progress_percentage,
  DROP COLUMN IF EXISTS monthly_required,
  DROP COLUMN IF EXISTS daily_required;

CREATE OR REPLACE VIEW public.v_goals_current_month AS
WITH current_period AS (
    SELECT to_char((now() AT TIME ZONE 'America/Bogota'), 'YYYY-MM') AS period
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
       
       CASE 
           WHEN g.target_date IS NULL OR g.target_amount <= 0 THEN NULL
           ELSE ROUND((GREATEST(g.target_amount - g.current_amount, 0::numeric) / 
                       GREATEST((g.target_date - (now() AT TIME ZONE 'America/Bogota')::date)::numeric / 30.0, 1::numeric))::numeric, 2)
       END AS monthly_required,
       
       CASE 
           WHEN g.target_date IS NULL OR g.target_amount <= 0 THEN NULL
           ELSE ROUND((GREATEST(g.target_amount - g.current_amount, 0::numeric) / 
                       GREATEST((g.target_date - (now() AT TIME ZONE 'America/Bogota')::date)::numeric, 1::numeric))::numeric, 2)
       END AS daily_required,
       
       CASE
           WHEN g.target_amount > 0 THEN LEAST(ROUND(((g.current_amount / g.target_amount) * 100)::numeric, 2), 100::numeric)
           ELSE 0::numeric
       END AS progress_percentage,
       
       g.status,
       cp.period,
       COALESCE(gcm.contributed_this_period, 0::numeric) AS contributed_this_period,
       
       CASE 
           WHEN g.target_date IS NULL OR g.target_amount <= 0 THEN 0::numeric
           ELSE GREATEST(
               ROUND((
                   (g.target_amount - g.current_amount + COALESCE(gcm.contributed_this_period, 0::numeric)) / 
                   GREATEST((g.target_date - (now() AT TIME ZONE 'America/Bogota')::date)::numeric / 30.0, 1::numeric)
               )::numeric, 2) - COALESCE(gcm.contributed_this_period, 0::numeric), 
               0::numeric
           )
       END AS remaining_required_this_period

FROM goals g
CROSS JOIN current_period cp
LEFT JOIN goal_contributions_month gcm 
  ON gcm.goal_id = g.id AND gcm.user_id = g.user_id AND gcm.period = cp.period
WHERE g.is_active = true 
  AND g.status IN ('active', 'paused');

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
       to_char((now() AT TIME ZONE 'America/Bogota'), 'YYYY-MM') AS period,
       now() AS calculated_at
FROM users u
LEFT JOIN account_totals at ON at.user_id = u.id
LEFT JOIN card_totals ct ON ct.user_id = u.id
LEFT JOIN obligation_totals ot ON ot.user_id = u.id
LEFT JOIN goal_totals gt ON gt.user_id = u.id
WHERE u.status = 'active';

COMMIT;
