# 02 — Domain Map

El backend está segmentado en dominios funcionales que interactúan a través de interfaces de servicio bien definidas.

## Core Domains

### 1. Users
- Gestión de identidades del sistema.
- Mapea un usuario externo (Supabase `auth_id`) a una entidad interna robusta (`users.id`).
- Define ownership sobre todos los demás recursos.

### 2. Accounts
- Cuentas de fondeo (origen de dinero) y balances disponibles.
- Representa el dinero "líquido" del usuario.

### 3. Ledger
- Sistema de trazabilidad.
- Generación y almacenamiento de `financial_events`.
- Todo flujo de dinero (pago, transferencia, cargo) se asienta aquí.

### 4. Obligations (V1.7/V1.8 funcional)
- El core de pasivos. Representa deudas generales y préstamos con calendario estructurado o variables.
- Domina la abstracción de `Obligation` (plantilla de la deuda) y `ObligationPeriod` (ciclo operativo con lifecycle propio).
- Responsable de aplicar reglas FIFO y Smart Payment.

### 5. Credit Cards (V1.5)
- Modelado complejo específico de tarjetas de crédito.
- Transacciones, proyecciones de intereses, fechas de corte y generación de estados de cuenta.

### 6. Goals
- Apartados o metas de ahorro. Dinero retenido que reduce la liquidez operativa pero permanece a nombre del usuario.

### 7. FX (Foreign Exchange) Engine
- Proveedor interno de tipos de cambio (USD ↔ COP).
- Maneja peticiones externas a DólarAPI.
- Mantiene la tabla `exchange_rates` como caché y snapshots persistentes.

### 8. Intelligence
- Módulo de agregación. Consolida la visión integral del usuario.
- Genera el contexto narrativo (Summary/Context) utilizado por Agentes LLM.

## Relaciones Principales
- **Accounts ↔ Ledger ↔ Obligations:** Un pago debita una *Account*, crea un evento en *Ledger*, e impacta y salda un período en *Obligations*.
- **FX ↔ Obligations:** Un pago cross-currency (ej. pagar deuda en USD usando cuenta en COP) primero invoca *FX* para validar un snapshot congelado, antes de calcular el débito real en *Accounts*.

---
*Last verified against `367ebdc`*