# Nexum V1.5 — Fixed Obligation Contract Hotfix Deploy Report

## 1. Executive Summary
El hotfix backend `ddb10c7` ha sido desplegado con éxito en el VPS de producción. Este despliegue incorpora la optimización de contratos de pago para obligaciones fijas (`fixed_full_payment`), permitiendo que el payload de pago envíe `amount = null` para que el backend determine automáticamente y deduzca de manera exacta el valor correspondiente (con conversión multimoneda si fuera necesario). También incluye validaciones de seguridad de tipos para pagos anticipados de tarjeta con monto nulo (`amount=None`).

## 2. Commit Deployed
- **Local Commit:** `ddb10c7`
- **VPS Commit:** `ddb10c7` (Fast-forwarded on main branch)

## 3. Migration Status
- **Migrations required:** no
- **Migrations executed:** no

## 4. Docker / Runtime
- **Container Name:** `nexum_backend_api`
- **Status:** Up About a minute (healthy)
- **Rebuilt & Restarted:** sí

## 5. Health / Readiness
- **URL Health:** `https://api.nexum.lytrium.tech/health` -> `{"status":"ok","service":"nexum-backend"}`
- **URL Readiness:** `https://api.nexum.lytrium.tech/health/readiness` -> `{"status":"ok","service":"nexum-backend"}`

## 6. Smokes
Debido a la ausencia de un token de QA seguro para producción, los smokes se limitaron a las validaciones de conectividad, salud e integridad del runtime de Docker. Todos los tests de la suite pasaron localmente al 100% (226/226).

## 7. OpenAPI Impact
El esquema `ObligationPaymentCreate` se actualizó indicando que `amount` es un campo opcional (`Decimal | None`). Esto es seguro para el frontend, permitiendo la intención de pago completo sin requerir cálculos en la UI.

## 8. Issues
- **Issues Found:** Ninguna. El despliegue y reinicio se completaron sin incidentes.

## 9. Runtime QA Retest Plan
Con el backend actualizado y en estado saludable, es seguro proceder al commit y push de las modificaciones correspondientes del frontend en su repositorio, procediendo con la verificación final de QA en Runtime.
