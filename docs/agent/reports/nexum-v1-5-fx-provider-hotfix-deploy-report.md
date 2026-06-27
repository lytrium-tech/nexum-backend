# Nexum V1.5 — FX Provider Hotfix Deploy Report

## 1. Executive Summary
On June 27, 2026, Backend Hotfix `1be2941` was successfully deployed to production to resolve the FX provider runtime blocker that caused cross-currency transfers and contributions to fail with DNS errors. The API is now fully operational and ready for manual Runtime QA.

## 2. Commit Deployed
- **Commit Hash**: `1be2941`
- **Origin Branch**: `main`
- **Target VPS Path**: `/opt/nexum-backend`

## 3. Migration Status
- **Migrations Required**: No database migrations were needed for this hotfix.

## 4. Docker / Runtime
- **Container**: `nexum_backend_api`
- **Build**: Successfully rebuilt from the updated source image.
- **Docker Status**: `healthy` (running for >15s on restart).

## 5. Health / Readiness
- **Health Endpoint**: `https://api.nexum.lytrium.tech/health` -> `{"status":"ok","service":"nexum-backend"}`
- **Readiness Endpoint**: `https://api.nexum.lytrium.tech/health/readiness` -> `{"status":"ok","service":"nexum-backend"}`

## 6. FX Provider Verification
Verified internet and DNS connectivity from within the production container to the correct Dolar API Colombia endpoint:
- **Host**: `co.dolarapi.com` resolved to IP `188.114.96.2`
- **Endpoint**: `https://co.dolarapi.com/v1/cotizaciones/usd`
- **Status**: HTTP 200 OK
- **Payload Sample**: `{"moneda": "USD", "compra": 3451.12, "venta": 3454.62}`

## 7. Smokes
Smoke testing was limited to container-level network/API validations due to the absence of a production QA token. External verification of endpoints and container logs confirm the provider resolves cleanly.

## 8. Issues
- **None**: No issues or regressions detected. All backend services are healthy.

## 9. Runtime QA Next Step
Instruct the frontend/QA team to re-run cross-currency transactions (COP -> USD transfers and Macbook goal contributions) to verify they now succeed and fetch correct live rates without DNS resolution exceptions.
