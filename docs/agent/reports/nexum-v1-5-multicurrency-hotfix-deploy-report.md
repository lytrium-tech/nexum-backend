# Nexum V1.5 — Multi-Currency Runtime QA Hotfix Deploy Report

## 1. Executive Summary
El hotfix backend `8a52b64` fue desplegado exitosamente en el VPS productivo. Se verificó el reinicio de los contenedores, la salud de la aplicación y la disponibilidad de los endpoints de producción. Este hotfix resuelve los blockers financieros y crashes de servidor de la versión V1.5.

## 2. Commit Deployed
- **Local Commit:** `8a52b64`
- **VPS Commit:** `8a52b64` (Fast-forwarded on main branch)

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
Debido a la ausencia de un token de QA seguro para producción, los smokes se limitaron a las validaciones de conectividad, salud e integridad del runtime de Docker. Todos los tests de la suite pasaron localmente al 100% (215/215).

## 7. Issues
- **Issues Found:** Ninguna. El despliegue se completó sin incidentes.

## 8. Runtime QA Retest Plan
Se solicita al equipo de QA realizar la re-evaluación en la aplicación Web/Mobile (Frontend) de los siguientes casos para validar la corrección de los bugs:
1. Pago de obligación COP desde cuenta COP (sin conversión).
2. Pago de obligación COP desde cuenta USD (conversión inversa USD -> COP).
3. Pago de obligación USD desde cuenta COP (conversión directa COP -> USD).
4. Pago de tarjeta de crédito COP desde cuenta USD (conversión inversa USD -> COP).
5. Pago de tarjeta de crédito USD desde cuenta COP (conversión directa COP -> USD).
6. Abono anticipado a una compra de tarjeta de crédito que no tenga abonos anteriores.
7. Intentar crear una obligación con un nombre que ya esté activo (confirmar que retorna una alerta de conflicto en lugar de un error de servidor).
