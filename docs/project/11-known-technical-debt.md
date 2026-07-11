# 11 — Known Technical Debt

## Deuda Operativa y Contractual
1. **NEXUM_OBLIGATIONS_V17_ENABLED:** Este Feature Flag se utiliza para bloquear (mediante HTTP 403) los endpoints V1.7. Es una medida temporal introducida durante migraciones. Debe ser removido del código de producción una vez que el cliente V1.7 esté completamente consolidado. *No se debe reemplazar por otro flag de versión.*
2. **Campos Legacy en Payloads:** Los campos `source_amount` y `quote_id` aún permanecen presentes como `Optional` en los esquemas de creación de pagos (`openapi.json`). Su persistencia es intencional por ahora para preservar la compatibilidad con clientes más antiguos en fase de transición. Serán deprecados.
3. **Endpoint de Preview Legacy:** Endpoints como el antiguo preview de FX siguen en el código fuente para dar soporte transitorio. Serán deprecados.
4. **Nomenclatura V1.7 vs V1.8:** Se generó deuda de nomenclatura en los commits e historial (refiriéndose a características nuevas como "V1.8"), aunque todo el core operativo sigue enrutado bajo `/api/v1.7`. Esto puede causar confusión cognitiva.

## Documentación
- Los reportes históricos ubicados en `docs/agent/reports/` no han sido limpiados o archivados formalmente. Permanecen como una "caja negra" de evidencia histórica que no debería usarse como fuente canónica técnica de cara al futuro.

---
*Last verified against `367ebdc`*
