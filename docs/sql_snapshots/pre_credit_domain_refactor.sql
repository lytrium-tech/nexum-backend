-- Snapshot of views before Credit Domain refactor

-- v_credit_card_debt
CREATE OR REPLACE VIEW public.v_credit_card_debt AS
WITH card_movements AS (
  SELECT 
    cct.user_id,
    cct.credit_card_id,
    sum(
      CASE
        WHEN cct.type = 'purchase'::text THEN COALESCE(cct.total_with_interest, cct.amount)
        ELSE 0::numeric
      END
    ) AS purchases_total,
    sum(
      CASE
        WHEN cct.type = 'payment'::text THEN cct.amount
        ELSE 0::numeric
      END
    ) AS payments_total,
    sum(
      CASE
        WHEN cct.type = 'purchase'::text THEN COALESCE(cct.monthly_amount, COALESCE(cct.total_with_interest, cct.amount) / GREATEST(cct.installments_total, 1)::numeric)
        ELSE 0::numeric
      END
    ) AS monthly_purchases_total
  FROM credit_card_transactions cct
  GROUP BY cct.user_id, cct.credit_card_id
)
SELECT 
  cc.user_id,
  cc.id AS credit_card_id,
  cc.name AS credit_card_name,
  cc.bank,
  cc.cutoff_day,
  cc.due_day,
  GREATEST(COALESCE(cm.purchases_total, 0::numeric) - COALESCE(cm.payments_total, 0::numeric), 0::numeric) AS credit_card_debt,
  CASE
    WHEN GREATEST(COALESCE(cm.purchases_total, 0::numeric) - COALESCE(cm.payments_total, 0::numeric), 0::numeric) <= 0::numeric THEN 0::numeric
    ELSE LEAST(COALESCE(cm.monthly_purchases_total, 0::numeric), GREATEST(COALESCE(cm.purchases_total, 0::numeric) - COALESCE(cm.payments_total, 0::numeric), 0::numeric))
  END AS monthly_cc_payment,
  COALESCE(cm.purchases_total, 0::numeric) AS purchases_total,
  COALESCE(cm.payments_total, 0::numeric) AS payments_total
FROM credit_cards cc
LEFT JOIN card_movements cm ON cm.credit_card_id = cc.id
WHERE cc.is_active = true;

-- v_financial_snapshot_current_month
CREATE OR REPLACE VIEW public.v_financial_snapshot_current_month AS
WITH account_totals AS (
  SELECT accounts.user_id, sum(accounts.balance) AS available_real
  FROM accounts
  WHERE accounts.is_active = true
  GROUP BY accounts.user_id
), card_totals AS (
  SELECT v_credit_card_debt.user_id, sum(v_credit_card_debt.credit_card_debt) AS credit_card_debt, sum(v_credit_card_debt.monthly_cc_payment) AS monthly_cc_payment
  FROM v_credit_card_debt
  GROUP BY v_credit_card_debt.user_id
), obligation_totals AS (
  SELECT v_pending_obligations_current_month.user_id, sum(CASE WHEN v_pending_obligations_current_month.is_pending = true THEN v_pending_obligations_current_month.amount ELSE 0::numeric END) AS pending_obligations_total
  FROM v_pending_obligations_current_month
  GROUP BY v_pending_obligations_current_month.user_id
), goal_totals AS (
  SELECT v_goals_current_month.user_id, sum(v_goals_current_month.remaining_required_this_period) AS monthly_goals_required_remaining
  FROM v_goals_current_month
  GROUP BY v_goals_current_month.user_id
)
SELECT 
  u.id AS user_id,
  u.name AS user_name,
  COALESCE(at.available_real, 0::numeric) AS available_real,
  COALESCE(ct.credit_card_debt, 0::numeric) AS credit_card_debt,
  COALESCE(ct.monthly_cc_payment, 0::numeric) AS monthly_cc_payment,
  COALESCE(ot.pending_obligations_total, 0::numeric) AS pending_obligations_total,
  COALESCE(gt.monthly_goals_required_remaining, 0::numeric) AS monthly_goals_required_remaining,
  COALESCE(at.available_real, 0::numeric) - COALESCE(ct.monthly_cc_payment, 0::numeric) - COALESCE(ot.pending_obligations_total, 0::numeric) AS safe_money,
  COALESCE(at.available_real, 0::numeric) - COALESCE(ct.monthly_cc_payment, 0::numeric) - COALESCE(ot.pending_obligations_total, 0::numeric) - COALESCE(gt.monthly_goals_required_remaining, 0::numeric) AS free_money,
  to_char(now() AT TIME ZONE 'America/Bogota'::text, 'YYYY-MM'::text) AS period,
  now() AS calculated_at
FROM users u
LEFT JOIN account_totals at ON at.user_id = u.id
LEFT JOIN card_totals ct ON ct.user_id = u.id
LEFT JOIN obligation_totals ot ON ot.user_id = u.id
LEFT JOIN goal_totals gt ON gt.user_id = u.id
WHERE u.status = 'active'::text;
