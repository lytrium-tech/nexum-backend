# Nexum V1.5 — Fixed Obligation & Pay Early Hotfix Deploy Report

## 1. Executive Summary
El hotfix backend `ad745eb` fue desplegado exitosamente en el VPS de producción. Se verificaron el inicio y estado saludable del contenedor Docker `nexum_backend_api`, la disponibilidad e integridad del servidor principal y el estado de los endpoints `/health` y `/health/readiness`. Este hotfix resuelve de forma definitiva los desajustes de validación multimoneda en obligaciones fijas, el crash 500 al realizar pagos anticipados de tarjetas sin especificar monto (`amount=None`), y las validaciones de elegibilidad de pagos anticipados.

## 2. Commit Deployed
- **Local Commit:** `ad745eb`
- **VPS Commit:** `ad745eb` (Fast-forwarded on main branch)

## 3. Migration Status
- **Migrations required:** no
- **Migrations executed:** no

## 4. Docker / Runtime
- **Container Name:** `nexum_backend_api`
- **Status:** Up (healthy)
- **Rebuilt & Restarted:** sí

## 5. Health / Readiness
- **URL Health:** `https://api.nexum.lytrium.tech/health` -> `{"status":"ok","service":"nexum-backend"}`
- **URL Readiness:** `https://api.nexum.lytrium.tech/health/readiness` -> `{"status":"ok","service":"nexum-backend"}`

## 6. Smokes
Debido a la ausencia de un token de QA seguro para producción, los smokes se limitaron a las validaciones de conectividad, salud e integridad del runtime de Docker. Todos los tests de la suite pasaron localmente al 100% (222/222).

## 7. OpenAPI Impact
Se actualizó la especificación OpenAPI (`openapi.json`) exponiendo el campo computado `is_pay_early_eligible` como un booleano de solo lectura dentro de la respuesta de cuotas de tarjeta (`CreditCardInstallmentRead`), facilitando que el frontend lo utilice para determinar el estado visual del botón/acción "Pagar anticipadamente".

## 8. Issues
- **Issues Found:** Ninguna. El despliegue y reinicio se completaron sin incidentes.

## 9. Runtime QA Retest Plan
Se solicita al equipo de QA realizar la re-evaluación en la aplicación Web/Mobile (Frontend) de los siguientes casos para validar la corrección de los bugs:
1. Pagar una obligación fija COP de 10.000 COP usando una cuenta USD con saldo suficiente (ej. 5 USD). Confirmar que el pago se realiza correctamente y se deduce el equivalente en USD sin arrojar "Saldo insuficiente".
2. Intentar pagar la obligación fija COP con un monto menor (ej. 1 USD) desde la cuenta USD, y confirmar que retorna un error de desajuste controlado indicando que la cantidad no cubre la cuota, en lugar de error de fondos de la cuenta.
3. Realizar un abono anticipado (`pay_early`) a una compra multicantidad de tarjeta sin indicar monto (`amount=None`), y confirmar que se amortiza la totalidad de la deuda de forma correcta sin causar un error de servidor (500).
4. Verificar que las compras de una sola cuota (cuotas_total = 1) no habilitan el botón "Pagar anticipadamente" y retornan error controlado en caso de intento manual.
5. Verificar que compras sin cuotas futuras pendientes (todas las cuotas facturadas o ya pagadas) no habilitan la opción de pago anticipado.
