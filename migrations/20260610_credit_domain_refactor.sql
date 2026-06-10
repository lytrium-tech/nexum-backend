-- Migration: Credit Domain Refactor

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
        WHEN cct.type = 'purchase'::text 
         AND (cct.occurred_at::date + (GREATEST(cct.installments_total, 1) || ' months')::interval) >= CURRENT_DATE
        THEN ROUND(COALESCE(cct.monthly_amount, COALESCE(cct.total_with_interest, cct.amount) / GREATEST(cct.installments_total, 1)::numeric), 2)
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
