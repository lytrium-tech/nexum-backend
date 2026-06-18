# Backend V1.1 Sprint 3 — Goals Consistency Report

## 1. Executive Summary
Se ha implementado satisfactoriamente el Sprint 3 para asegurar la coherencia financiera e integridad en el ciclo de vida de las metas financieras. El sistema ahora es capaz de diferenciar entre metas flexibles y metas con fecha objetivo, calcular la cantidad requerida para el periodo actual dinámicamente y computar aportes extras sin bloqueo. Todo esto se integra directamente con la vista "Snapshot Truth" (verdad financiera de Nexum) al reflejar las necesidades de meta del mes como `committed_outflows`. 

## 2. Implemented Scope
- Soporte para calcular el `monthly_required` en base al monto restante y la fecha límite.
- Distinción entre metas "flexibles" y con fechas, calculando lógicas específicas según su estado.
- Inserción del estado periódico actual en la entidad base (`period_status` y campos relacionados).
- Integración a `snapshot.truth` agregando las necesidades obligatorias de ahorro al total del dinero comprometido.
- Se mantuvo intacta la compatibilidad con el Ledger sin registrar aportes de metas como "gastos de consumo".

## 3. Goals API
La respuesta del objeto de lectura (`GoalRead`) devuelto por los endpoints de `GET` y al operar la meta fue expandida sin romper el contrato previo, añadiendo:
- `monthly_required`: (Decimal | None) Requerido asintótico mensual.
- `required_this_period`: (Decimal) Dinero que se debe inyectar este periodo.
- `contributed_this_period`: (Decimal) Suma de aportes recibidos en el mes corriente.
- `remaining_required_this_period`: (Decimal) Faltante para cubrir el periodo.
- `period_status`: (str) Estado del requerimiento (`flexible`, `pending`, `partial`, `covered`, `completed`).
- `is_flexible`: (bool) Identificador de meta con aportes no-periódicos libres.

## 4. Monthly Requirement Logic
El requerimiento mensual (`monthly_required`) se recalcula en caliente como `remaining_amount / months_left`.
La cantidad faltante del mes en curso (`remaining_required_this_period`) se calcula deduciendo `contributed_this_period` a `required_this_period`.

## 5. Flexible Goals
Toda meta creada sin especificar `target_date` será `is_flexible = True`. Estas metas devuelven `required_this_period = 0.00` y reportan `period_status = "flexible"`. Además, no sumarán carga al flujo de liquidez comprometido en `Snapshot`.

## 6. Contributions Behavior
El modelo subyacente para los aportes (`goal_contributions`) genera un evento `goal_contribution` en el Ledger descontando liquidez de la cuenta en cuestión. El evento incrementa el `current_amount` sin distorsionar el consolidado mensual global de ingresos y gastos de consumo. Crear metas por sí solas no mueve dinero ni altera el flujo general de los usuarios.

## 7. Extra Contributions
El sistema no impone bloqueos sobre-cubrir o pagar anticipadamente para las metas. Si en un mes se aporta por encima del requerido, el mes se mostrará como `covered`, y el requerimiento por mes restante se ajustará a la baja automáticamente para el siguiente cálculo. Se pueden aportar pagos múltiples en cualquier momento, siempre y cuando no se exceda el total final global de la meta.

## 8. Snapshot Truth Integration
En la vista general del estado (`GET /api/v1/intelligence/snapshot`), el apartado `truth` ahora devuelve `goals_required_this_period` que computa la sumatoria del `remaining_required_this_period` de todas las metas activas del usuario (ignorando las que ya están completadas, cubiertas del mes, o son flexibles). Este saldo está añadido por defecto a `committed_outflows` reduciendo adecuadamente el dinero "libre" del usuario (`free_money`).

## 9. Ledger Compatibility
Todo es 100% retrocompatible con la V1.0. No fue necesario modificar los eventos previamente registrados. `GET /api/v1/ledger/summary` excluye dinámicamente `goal_contribution` del balance total de gastos netos (`total_expenses`).

## 10. Tests
Se ejecutó la suite completa de unit testing en backend que corre a través de Pydantic usando Pytest (`pytest tests/ -v`). Se validaron integraciones profundas en el modelo asíncrono para verificar que el servicio no introdujo inestabilidades generales. Todos los tests superados sin errores.

## 11. Smoke Tests
Se introdujo `scripts/smoke/smoke_goals_consistency_v11.py`, probando los flujos vitales e indirectos (como afectaciones a "Truth" y "Ledger"). Todas las revisiones de regresión contra Accounts, Categories, History y Traceability pasaron con normalidad y los comandos informaron `=== SMOKE TEST: PASSED ===`.

## 12. Migrations
No se generaron o requirieron alteraciones o migraciones DDL / Destructivas a los esquemas de bases de datos para soportar los nuevos campos, ya que toda la inteligencia de metas se implementó de forma derivada y relacional desde el Backend de manera determinística, protegiendo los datos antiguos y eliminando cuellos de botella por consistencia en cache/storage.

## 13. Deploy
Se consolidó exitosamente el commit `feat: implement goals consistency v1.1` (ref. `7925d7f`) empujado de manera limpia al VPS remoto y la imagen Docker está construida y desplegada sin errores o warnings de servicios.

## 14. Health/readiness
Comprobación superada:
```json
{"status":"ok","service":"nexum-backend"}
```

## 15. Known Limitations
- No existe el mecanismo de "recalculo automático a 12 meses" como recomendación inicial propuesto para metas flexibles. Por orden de negocio, quedó mapeado hacia V1.2+.
- El Snapshot Truth agrupa metas por mes calendario, lo que funciona actualmente según la lógica del servicio, pero se sugiere no desacoplar o cruzar metas de tiempo menores al mes en la versión web para evitar confusiones de usabilidad.

## 16. Recommendation for Sprint 4
Iniciar el flujo del Sprint 4: `Obligations Safety` para estabilizar cómo las metas interactúan con obligaciones financieras a futuro antes de lanzar la versión del frontend definitivo. Validar qué tanto peso de la capa de API está afectando las latencias directas tras inyectar estos cómputos inter-relacionales.
