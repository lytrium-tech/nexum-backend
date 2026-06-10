-- Up Migration: Intelligence Consumption Refactor (Phase 10)

CREATE OR REPLACE VIEW public.v_consumption_summary_current_month AS
 WITH current_period AS (
         SELECT to_char((now() AT TIME ZONE 'America/Bogota'::text), 'YYYY-MM'::text) AS period
        ), consumption_aggregates AS (
         SELECT fe.user_id,
            cp.period,
            sum(
                CASE
                    WHEN (fe.event_type = ANY (ARRAY['expense'::text, 'obligation_payment'::text])) THEN fe.amount
                    ELSE (0)::numeric
                END) AS cash_consumption_outflow,
            sum(
                CASE
                    WHEN (fe.event_type = 'credit_card_purchase'::text) THEN fe.amount
                    ELSE (0)::numeric
                END) AS credit_card_consumption_committed
           FROM (financial_events fe
             CROSS JOIN current_period cp)
          WHERE (fe.period = cp.period)
          GROUP BY fe.user_id, cp.period
        )
 SELECT u.id AS user_id,
    COALESCE(ca.period, to_char((now() AT TIME ZONE 'America/Bogota'::text), 'YYYY-MM'::text)) AS period,
    COALESCE(ca.cash_consumption_outflow, (0)::numeric) AS cash_consumption_outflow,
    COALESCE(ca.credit_card_consumption_committed, (0)::numeric) AS credit_card_consumption_committed,
    (COALESCE(ca.cash_consumption_outflow, (0)::numeric) + COALESCE(ca.credit_card_consumption_committed, (0)::numeric)) AS total_consumption_committed,
    now() AS calculated_at
   FROM (users u
     LEFT JOIN consumption_aggregates ca ON ((u.id = ca.user_id)))
  WHERE (u.status = 'active'::text);
