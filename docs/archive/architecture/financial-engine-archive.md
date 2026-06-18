# Nexum Financial Engine

## 1. Propósito

Nexum no es una app de gastos.

Nexum es un sistema operativo financiero personal impulsado por IA, diseñado para ayudar al usuario a entender:

* Cuánto dinero tiene realmente.
* Cuánto puede usar sin ponerse en riesgo.
* Cuánto está construyendo patrimonio.
* Cuánto debe.
* Cuánto dinero está prestando o recibiendo.
* Qué decisiones financieras debería tomar.

La interfaz no debe mostrar números bonitos sin significado. Cada métrica debe venir de una lógica financiera clara.

---

## 2. Principio central

Nexum separa el dinero en capas.

* No todo "outflow" significa pérdida.
* No todo "inflow" significa riqueza.
* No todo saldo disponible significa dinero libre.

Por eso el sistema se organiza en estas capas:

1. Liquidity Layer
2. Wealth Building Layer
3. Debt Layer
4. Credit Card Layer
5. Loans Layer
6. Obligations Layer
7. Intelligence Layer

---

## 3. Liquidity Layer

### Qué representa
La liquidez representa el dinero disponible en cuentas reales:

* Nequi
* Bancolombia
* Efectivo
* Wallets
* Cuentas bancarias
* Cuentas de ahorro

### Métrica base
available_real = sum(accounts.balance)

### Safe Money
Dinero que existe, pero que debe protegerse porque tiene compromisos cercanos.
safe_money = available_real - pending_obligations_total - monthly_cc_payment

### Free Money
Dinero que el usuario puede usar con menor riesgo después de separar obligaciones, pagos de tarjeta y metas del mes.
free_money = safe_money - monthly_goals_required_remaining

### Regla
La liquidez responde: ¿Cuánto dinero puedo usar hoy sin romper mi mes?

---

## 4. Wealth Building Layer

### Qué representa
Esta capa mide la creación de patrimonio o avance financiero.

Incluye:
* Aportes a metas.
* Ahorro.
* Inversión futura.
* Reducción de deuda.
* Acumulación de activos.
* Dinero prestado a otras personas como cuenta por cobrar.

### Regla clave
Un aporte a una meta NO debe tratarse emocionalmente como pérdida.

Ejemplo:
Ingreso: $3.000.000
Gasto real: $1.000.000
Aporte a meta: $1.000.000

El usuario no está "peor". Está convirtiendo liquidez en patrimonio futuro.

### Wealth Allocation
wealth_allocation = goal_contributions + savings + investments + debt_reduction

### Wealth Velocity
Wealth Velocity mide qué tan rápido el usuario convierte ingresos en progreso patrimonial. Una primera versión puede ser:
wealth_velocity = wealth_allocation / monthly_income

No debe usar fórmulas arbitrarias tipo: (inflow / outflow) * 20. Eso es débil y no representa riqueza real.

---

## 5. Consumption Layer

### Qué representa
Consumo es dinero que sale y no construye patrimonio.

Incluye:
* Comida.
* Transporte.
* Ocio.
* Suscripciones.
* Compras diarias.
* Pagos operativos.
* Obligaciones consumidas.

### Consumption Outflow
consumption_outflow = expenses + obligation_payments

No debería incluir: goal_contribution, investment, savings transfer, dinero prestado a otra persona, o pagos de deuda si se quieren analizar como debt service.

### Consumption Rate
consumption_rate = consumption_outflow / monthly_income

Esta métrica responde: ¿Qué porcentaje de mis ingresos estoy consumiendo?

---

## 6. Credit Card Layer

### Principio
Una compra con tarjeta de crédito NO reduce liquidez inmediatamente. Por eso:
credit_card_purchase = neutral cashflow
Pero sí aumenta deuda.

### Compra con tarjeta
Impacto:
cash impact: 0
debt impact: +amount
event_type: credit_card_purchase
direction: neutral

### Pago de tarjeta
Impacto:
cash impact: -amount
debt impact: -amount
event_type: credit_card_payment
direction: outflow

### Regla importante
El pago de tarjeta sí reduce liquidez, pero no debe mezclarse con consumo del mes si la compra ocurrió en otro momento. Debe verse como: debt_service

### Métricas
credit_card_debt = purchases_total - payments_total
monthly_cc_payment = pago requerido del periodo

---

## 7. Debt Layer

### Qué representa
La capa de deuda mide obligaciones financieras que reducen libertad futura.

Incluye:
* Tarjetas de crédito.
* Préstamos recibidos.
* Deudas personales.
* Créditos.
* Intereses.
* Cuotas futuras.

### Debt Service
debt_service = credit_card_payments + loan_payments + interest_payments

Esta métrica responde: ¿Cuánto de mi liquidez se va en pagar deuda?

### Debt Pressure
debt_pressure = debt_service / monthly_income

---

## 8. Loans Layer

Esta capa maneja préstamos entre personas.

### Caso A: Yo presto dinero a alguien
Ejemplo: Le presté $100.000 a Juan desde Nequi.
Impacto:
cash impact: -100.000
receivable asset: +100.000
net worth impact: 0

El usuario tiene menos liquidez, pero no necesariamente perdió patrimonio. Debe aparecer como: Money lent / cuentas por cobrar

### Caso B: Me pagan un préstamo
Ejemplo: Juan me devolvió $100.000.
Impacto:
cash impact: +100.000
receivable asset: -100.000
net worth impact: 0

### Caso C: Yo recibo un préstamo
Ejemplo: Juan me prestó $100.000.
Impacto:
cash impact: +100.000
liability: +100.000
net worth impact: 0

El dinero aparece en liquidez, pero no debe confundirse con riqueza.

### Caso D: Yo pago un préstamo
Impacto:
cash impact: -amount
liability: -amount
net worth impact: 0

---

## 9. Obligations Layer

### Qué representa
Obligaciones son compromisos recurrentes o programados.

Ejemplos:
* Transporte.
* Arriendo.
* Internet.
* Suscripciones importantes.
* Mensualidades.
* Pagos fijos.

### Regla
Una obligación activa sigue existiendo aunque se pague en el mes actual. El pago se registra en: obligation_payments
La obligación original sigue activa para meses futuros.

### Pending Obligations
pending_obligations_total = obligations activas sin pago registrado en el periodo actual

---

## 10. Financial Events como Ledger Universal

financial_events es el registro central de eventos financieros. No todo cálculo debe hacerse desde el frontend.

Regla:
financial_events = historia
views/RPC = inteligencia financiera
frontend = representación visual

### Event Types actuales
income
expense
credit_card_purchase
credit_card_payment
obligation_payment
goal_contribution
manual_adjustment

### Directions actuales
inflow
outflow
neutral

---

## 11. Reglas conceptuales por event_type

### income
liquidity: increases
wealth: may increase
cashflow: inflow

### expense
liquidity: decreases
wealth: decreases
cashflow: consumption_outflow

### credit_card_purchase
liquidity: no immediate change
debt: increases
cashflow: neutral
consumption: should be tracked separately as committed spending

### credit_card_payment
liquidity: decreases
debt: decreases
cashflow: debt_service

### goal_contribution
liquidity: decreases
wealth building: increases
cashflow: wealth_allocation, not consumption

### obligation_payment
liquidity: decreases
commitment: fulfilled
cashflow: committed_outflow

---

## 12. Intelligence Layer

La IA de Nexum no debe inventar insights. Todo insight debe tener:
1. Fuente de datos
2. Métrica base
3. Confianza
4. Acción sugerida

Ejemplo correcto:
Tu consumo en comida aumentó 18% frente al periodo anterior.
Fuente: financial_events filtrado por categoría comida.
Acción sugerida: define un límite semanal de $X.

Ejemplo incorrecto:
Tu ritmo financiero se ve saludable. (Sin datos que lo respalden)

---

## 13. Métricas principales del Home

### Primary Metric
free_money
Pregunta que responde: ¿Cuánto puedo usar sin comprometer mi mes?

### Secondary Metrics
available_real
safe_money
monthly_income
consumption_outflow
wealth_allocation
debt_service
credit_card_debt
pending_obligations_total
monthly_goals_required_remaining

### Tertiary Metrics
wealth_velocity
consumption_rate
debt_pressure
goal_progress
cashflow_trend

---

## 14. Realtime Principle

El sistema debe sentirse vivo. Cuando n8n registra un evento:
n8n -> Supabase -> frontend realtime -> UI actualizada

Realtime debe escuchar cambios en: financial_events, accounts, goals, obligations, credit_card_transactions, obligation_payments, goal_contributions.

Para MVP, puede escuchar financial_events como trigger principal y luego refrescar el snapshot completo.

---

## 15. Frontend Principle

El frontend NO debe contener lógica financiera compleja. No debe calcular: free_money, safe_money, debt, obligations pending, goal required amounts, wealth velocity final.

Eso debe venir de: SQL views, RPCs, Edge Functions, o backend workers.

El frontend solo debe: mostrar, animar, ordenar, escuchar realtime, manejar estados vacíos, y renderizar insights.

---

## 16. Views necesarias

### Actuales
v_account_balances
v_credit_card_debt
v_financial_snapshot_current_month
v_goals_current_month
v_pending_obligations_current_month

### Futuras sugeridas
v_cashflow_current_month
v_wealth_velocity_current_month
v_consumption_summary_current_month
v_debt_pressure_current_month
v_loans_summary
v_net_worth_snapshot

---

## 17. Pendientes importantes

### Base de datos
Agregar módulo de préstamos:
* contacts / people
* loans
* loan_payments
* receivables
* liabilities

### n8n
Mejorar validaciones:
* Metas duplicadas.
* Metas sin fecha o sin monto.
* Errores humanos.
* Pending actions.
* Respuestas claras.
* No errores genéricos.

### Frontend
Implementar:
* Realtime.
* History timeline.
* Assets/charts.
* Financial intelligence layer.
* Auth real.
* PWA.

---

## 18. Filosofía final

Nexum debe evitar que el usuario piense: "gasté / no gasté"
Y llevarlo a pensar: "¿mi dinero se está consumiendo, protegiendo o construyendo patrimonio?"
Ese es el núcleo del producto.