# 00 — Backend Overview

## Purpose
El backend de Nexum es la capa fundacional y la **única fuente de verdad financiera** de todo el ecosistema Nexum. Su responsabilidad principal es garantizar la integridad matemática, persistir estados financieros de forma segura, y proveer una API confiable para interfaces de usuario y agentes de inteligencia artificial.

## Scope
El alcance del backend incluye:
- Autenticación y resolución de identidad.
- Gestión de cuentas y sub-cuentas (Ledger).
- Manejo de deudas (Credit Cards, Obligations Core V1.7).
- Motor de divisas y tasa de cambio (FX Engine & Rate Snapshots).
- Provisión de contexto analítico (Intelligence context & Summary).
- Delegación y enrutamiento inteligente de pagos (Smart Payments, FIFO).

## Tech Stack
- **Framework:** FastAPI
- **Language:** Python 3.12+
- **ORM:** SQLAlchemy (Async)
- **Database:** PostgreSQL (alojada en Supabase)
- **Migrations:** Alembic
- **Validation:** Pydantic
- **Testing:** Pytest

## Core Principles
1. **Backend Calculates:** El frontend nunca decide cuánto se debe cobrar, qué tasa de cambio aplicar de forma autoritativa, ni a qué período se le asigna el dinero. El backend orquesta y ejecuta.
2. **Frontend Represents and Previews:** El frontend se encarga de proveer una excelente experiencia de usuario, pero las estimaciones financieras visuales que hace no son vinculantes para el backend.
3. **LLM Explains:** La Inteligencia Artificial consume los endpoints de inteligencia (context) para explicarle la situación al usuario de forma conversacional, pero no muta estados transaccionales complejos directamente ni realiza matemáticas críticas.
4. **Immutability:** Los eventos financieros (`financial_events`) y los estados transaccionales son inmutables para garantizar el rastro de auditoría.

## Relationships
- **Frontend / Móvil / WhatsApp:** Consumidores de los contratos expuestos a través de `/api/v1` y `/api/v1.7`.
- **Supabase:** Plataforma de PostgreSQL y Auth (identidad delegada).
- **DólarAPI (Externa):** Única fuente externa de tasas de cambio. El backend encapsula y abstrae las fallas de este proveedor a través de los Snapshots y un proveedor estático de desarrollo.

---
*Last verified against `367ebdc`*