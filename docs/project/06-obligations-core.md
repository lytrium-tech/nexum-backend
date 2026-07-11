# 06 — Obligations Core

## Concepto Central
El Obligations Core es el motor de pasivos introducido originalmente en V1.6, estabilizado en V1.7 y potenciado funcionalmente en "V1.8". Separa estáticamente el concepto de una deuda ("Plantilla") de sus instanciaciones en el tiempo ("Períodos").

## Entidades Principales
- **Obligation:** Define los parámetros inmutables o base de la deuda: nombre, balance total esperado, cuenta de fondeo default y moneda. Posee el tipo `fixed` (cuotas predefinidas, ej. préstamo) o `variable` (ej. servicios públicos cuyo monto varía).
- **ObligationPeriod:** La unidad operativa real. Cada mes/período, la obligación instancia un `ObligationPeriod`. Representa el ciclo de vida de lo que el usuario debe pagar ahora o en el futuro.

## Lifecycle de Períodos
Un período transita por estados finitos:
- **`pending`**: El período fue emitido pero aún no se vence.
- **`partially_paid`**: El usuario abonó dinero, pero no el monto total requerido.
- **`overdue`**: La fecha límite (`due_date`) ya expiró y aún existe saldo.
- **`paid`**: El saldo requerido se completó a cero (o el usuario sobre-pagó su requerimiento).
- **`skipped`**: El usuario deliberadamente saltó el pago (ej. reconoció que no aplicaba).
- **`cancelled`**: El sistema o el usuario anula el período antes de pagarlo.

## Generación y Batch Auto-Refresh
El motor (`PeriodEngine`) garantiza proactivamente que siempre exista un "current period" utilizable.
- Genera automáticamente un nuevo período (mes siguiente) al momento de saldar, saltar o cancelar el período activo actual.
- **Batch Refresh:** Existe una lógica de auto-refresco que evalúa silenciosamente si los períodos vencieron cronológicamente antes de entregar un summary de deudas, cambiando estados de `pending` a `overdue`.

## Dinámicas de Pago (V1.7 y V1.8 funcional)
- **Pagos a Período Específico:** El usuario puede enviar dinero explícitamente a un ID de período.
- **FIFO (First In, First Out):** Cuando existen múltiples períodos activos (ej. dos `overdue` y uno `pending`), el sistema de asignación de pagos toma el monto global y lo distribuye cronológicamente, llenando el más antiguo, y pasando el resto al siguiente (slices idempotentes).
- **Smart Payment:** Conocido operativamente como el "Smart one-button payment". Permite al usuario enviar dinero a la `Obligation` raíz sin pensar en períodos. El backend absorbe la responsabilidad, calcula qué períodos debe cobrar, aplica FIFO, cierra períodos llenos, y genera el próximo período necesario.

## Capacidades Analíticas
- **Overview:** Endpoint específico que unifica la respuesta con el ORM para tarjetas y obligaciones, ofreciendo una visión condensada.
- **Summary e Intelligence Context:** Rutas preparadas para proveer a la IA y los dashboards financieros un snapshot en texto rico y datos estructurados sobre el estado total pasivo del usuario.

## Compatibilidad de Modelos
Las rutas `/api/v1.7/obligations/` están diseñadas para retro-compatibilidad operativa temporal. Las etiquetas de versión como "V1.8" en commits refieren a las mejoras de Smart Payment y Overview, pero no bifurcaron la API pública.

---
*Last verified against `367ebdc`*
