-- Snapshot pre Intelligence Consumption Refactor (Fase 10)

CREATE OR REPLACE VIEW public.v_cashflow_current_month AS
 WITH current_period AS (
         SELECT to_char((now() AT TIME ZONE 'America/Bogota'::text), 'YYYY-MM'::text) AS period
        ), monthly_aggregates AS (
         SELECT fe.user_id,
            cp.period,
            sum(
                CASE
                    WHEN (fe.event_type = 'income'::text) THEN fe.amount
                    ELSE (0)::numeric
                END) AS monthly_income,
            sum(
                CASE
                    WHEN (fe.event_type = 'expense'::text) THEN fe.amount
                    ELSE (0)::numeric
                END) AS consumption_outflow,
            sum(
                CASE
                    WHEN (fe.event_type = 'goal_contribution'::text) THEN fe.amount
                    ELSE (0)::numeric
                END) AS wealth_allocation,
            sum(
                CASE
                    WHEN (fe.event_type = 'credit_card_payment'::text) THEN fe.amount
                    ELSE (0)::numeric
                END) AS debt_service,
            sum(
                CASE
                    WHEN (fe.event_type = 'obligation_payment'::text) THEN fe.amount
                    ELSE (0)::numeric
                END) AS committed_outflow
           FROM (financial_events fe
             CROSS JOIN current_period cp)
          WHERE (fe.period = cp.period)
          GROUP BY fe.user_id, cp.period
        )
 SELECT u.id AS user_id,
    COALESCE(ma.period, to_char((now() AT TIME ZONE 'America/Bogota'::text), 'YYYY-MM'::text)) AS period,
    COALESCE(ma.monthly_income, (0)::numeric) AS monthly_income,
    COALESCE(ma.consumption_outflow, (0)::numeric) AS consumption_outflow,
    COALESCE(ma.wealth_allocation, (0)::numeric) AS wealth_allocation,
    COALESCE(ma.debt_service, (0)::numeric) AS debt_service,
    COALESCE(ma.committed_outflow, (0)::numeric) AS committed_outflow,
    (((COALESCE(ma.consumption_outflow, (0)::numeric) + COALESCE(ma.wealth_allocation, (0)::numeric)) + COALESCE(ma.debt_service, (0)::numeric)) + COALESCE(ma.committed_outflow, (0)::numeric)) AS total_outflow,
    ((COALESCE(ma.monthly_income, (0)::numeric) - COALESCE(ma.consumption_outflow, (0)::numeric)) - COALESCE(ma.committed_outflow, (0)::numeric)) AS surplus_after_consumption,
    (COALESCE(ma.monthly_income, (0)::numeric) - (((COALESCE(ma.consumption_outflow, (0)::numeric) + COALESCE(ma.wealth_allocation, (0)::numeric)) + COALESCE(ma.debt_service, (0)::numeric)) + COALESCE(ma.committed_outflow, (0)::numeric))) AS net_liquidity_change,
    now() AS calculated_at
   FROM (users u
     LEFT JOIN monthly_aggregates ma ON ((u.id = ma.user_id)))
  WHERE (u.status = 'active'::text);


CREATE OR REPLACE VIEW public.v_consumption_current_month AS
 SELECT user_id,
    period,
    monthly_income,
    consumption_outflow,
    committed_outflow,
        CASE
            WHEN (monthly_income > (0)::numeric) THEN ((consumption_outflow + committed_outflow) / monthly_income)
            ELSE (0)::numeric
        END AS consumption_rate,
    calculated_at
   FROM v_cashflow_current_month;


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
