# 11 — Known Technical Debt

## Deuda Operativa y Contractual
1. **NEXUM_OBLIGATIONS_V17_ENABLED:** Este Feature Flag se utiliza para bloquear (mediante HTTP 403) los endpoints V1.7. Es una medida temporal introducida durante migraciones. Debe ser removido del código de producción una vez que el cliente V1.7 esté completamente consolidado. *No se debe reemplazar por otro flag de versión.*
2. **Campos Legacy en Payloads:** Los campos `source_amount` y `quote_id` aún permanecen presentes como `Optional` en los esquemas de creación de pagos de obligaciones (`openapi.json`). Su persistencia es intencional por ahora para preservar la compatibilidad con clientes más antiguos en fase de transición. Serán deprecados.
3. **Endpoint de Preview Legacy:** Endpoints como el antiguo preview de FX siguen en el código fuente para dar soporte transitorio. Serán deprecados.
4. **Nomenclatura V1.7 vs V1.8:** Se generó deuda de nomenclatura en los commits e historial (refiriéndose a características nuevas como "V1.8"), aunque todo el core operativo sigue enrutado bajo `/api/v1.7`. Esto puede causar confusión cognitiva.

## Credit Domain V2 (Canonical Closure) Technical Debt
1. **Campos de Compatibilidad Legacy en Credit Card Schemas:**
   - En `CreditCardRead`, `CreditCardStatusRead`, e `IntelligenceCreditCardRead`, los campos `current_debt`, `estimated_current_debt`, `monthly_cc_payment`, y `estimated_available_credit` permanecen presentes y marcados con `deprecated: true` en `openapi.json`.
   - En `CreditCardPurchaseCreate`, `amount` conserva su semántica histórica: monto contabilizado en moneda base de la tarjeta. Nunca significa monto original extranjero. Para cross-currency usar `original_amount` + `original_currency`; la respuesta incluye `booked_amount` + `booked_currency` y evidencia FX. La compatibilidad de `amount` se deprecará después de migrar consumidores.
   - Estos campos están programados para remoción definitiva una vez que el frontend complete su migración a los contratos canónicos V2 (`total_debt`, `available_credit`, `booked_amount`).
2. **Vistas SQL Dormidas:**
   - La vista histórica `v_credit_card_debt` permanece en el esquema de base de datos por retro-compatibilidad, pero ha sido desacoplada de la autoridad financiera en favor de `CreditCardService.get_credit_summary` y `get_card_debt_partitioned`.
3. **Consulta de índices FK a cardinalidad de producción:**
   - Goal 2 instaló los índices FK aditivos y verificó la migración/catalogación en PostgreSQL 17 de CT101. El costo comparativo del plan a cardinalidad de producción no se midió y sigue como seguimiento de rendimiento.
4. **Integración bancaria futura:**
   - No se implementó conectividad de bancos en Alpha. El contrato preserva hechos issuer-authoritative separados de datos manuales y de asignaciones Nexum-modeled.
5. **Estado de validación de Credit Goal 2:**
   - El gate físico Credit final pasó 29/29 y migraciones A/B/C pasaron. CT101 terminó detenido con schema sintético vacío. Evidencia: `docs/agent/reports/credit-domain-goal2-physical-release-gate-report.md`.

## Documentación
- Los reportes históricos ubicados en `docs/agent/reports/` no han sido limpiados o archivados formalmente. Permanecen como una "caja negra" de evidencia histórica que no debería usarse como fuente canónica técnica de cara al futuro.

---
*Last verified against Credit Goal 2 final implementation (`feature/credit-canonical-closure`)*
- Integrado con [Wallets V1](./13-accounts-wallets.md) y [Credit V2 Handoff](../handoff/backend/credit-canonical-v2-frontend-handoff.md).
