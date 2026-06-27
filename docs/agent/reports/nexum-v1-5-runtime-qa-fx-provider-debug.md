# Nexum V1.5 — FX Provider Runtime Blocker Debug Report

## 1. Context

During Runtime QA of Nexum V1.5, when attempting same-currency operations (e.g. COP -> COP, USD -> USD) everything worked, but cross-currency transactions (COP -> USD or USD -> COP) like goal contributions or transfers failed with:
`"No se pudo obtener la tasa de cambio: Error consultando Dólar API: [Errno -2] Name or service not known"`

## 2. Diagnostics & Findings

- **Root Cause**: 
  - `app/core/currency.py`'s `get_fx_rate()` was hardcoded to query `https://api.dolarapi.com/v1/dolares/oficial`.
  - The domain `api.dolarapi.com` is no longer active (returns `NXDOMAIN` on DNS resolution).
  - The official Dolar API Colombia provider runs at `https://co.dolarapi.com/v1/cotizaciones/usd` (as correctly declared in `app/core/config.py` settings under `DOLAR_API_BASE_URL: str = "https://co.dolarapi.com"`).
  - Because `get_fx_rate()` bypassed the configured `settings.DOLAR_API_BASE_URL` and used the hardcoded defunct URL, cross-currency calculations failed under all environments.
- **Connectivity Verification**:
  - Validated that the VPS host can resolve and query `https://co.dolarapi.com` successfully (returning HTTP 200).
  - Validated that the Docker container (`nexum_backend_api`) has full internet connectivity and correct DNS resolution for `co.dolarapi.com`.
- **Same-Currency Bypass**: Same-currency operations bypass the external FX query completely, which is why same-currency transfers/contributions did not trigger the error.
- **Error Sanitization**: The service layer originally captured `FXProviderError` and wrapped it in a `ForbiddenError` with the raw exception text, exposing technical connection details like `[Errno -2] Name or service not known` to the frontend.

## 3. Resolution Details

1. **Endpoint & Configuration Sync**:
   - Updated `get_fx_rate()` in `app/core/currency.py` to use `settings.DOLAR_API_BASE_URL` and `settings.FX_TIMEOUT_SECONDS`.
   - The query now successfully targets `https://co.dolarapi.com/v1/cotizaciones/usd`.
2. **Error Sanitization & Classification**:
   - Defined `FXProviderUnavailableError` (subclass of `InfrastructureError` / `NexumError`) in `app/core/errors.py`.
   - It maps to `HTTP 503 Service Unavailable` with `error_code="FX_PROVIDER_UNAVAILABLE"`.
   - The user-facing error message is: `"No pudimos consultar la tasa de cambio en este momento. Intenta de nuevo en unos momentos."`
   - Modified `app/goals/service.py` and `app/transfers/service.py` to catch `FXProviderError` and raise `FXProviderUnavailableError` (preventing raw `Errno` leakage).
3. **Unit Testing**:
   - Added unit tests in `tests/unit/test_currency.py` covering:
     - Same-currency bypass (ensuring no external HTTP call).
     - Unsupported currency pairs.
     - Successful DolarAPI mock parsing (`venta` rate and timestamp).
     - Clean `FXProviderError` propagation when DolarAPI connection fails.
   - All 201 backend unit tests pass successfully.
