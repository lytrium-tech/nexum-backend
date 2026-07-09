# Nexum V1.7 - Cross-Currency Preview & Payment Debug

## Context
During manual QA of the V1.7 Obligations flow, the USD -> COP preview and payment paths were blocked. 
When selecting a USD account to pay a COP obligation, the UI remained on "Calculando conversión..." and the "Confirmar pago" button remained disabled.

## Root Cause
The `POST /api/v1.7/obligations/{obligation_id}/periods/{period_id}/payments/preview` endpoint was crashing with a `500 Internal Server Error`.
The exact backend stack trace revealed:
```
  File "/app/app/obligations/service_v17.py", line 225, in pay_preview_specific_period
    from app.fx.provider import DummyFxProvider
ImportError: cannot import name 'DummyFxProvider' from 'app.fx.provider' (/app/app/fx/provider.py)
```
The provider `DummyFxProvider` did not exist in `provider.py`, resulting in an `ImportError`. This crashed the preview request, leaving the frontend without a `quote_id` and therefore unable to proceed to the payment request.

## Fix
In `app/obligations/service_v17.py`, replaced `DummyFxProvider` with `StaticFxRateProvider` which is the correct class name for the local fallback provider.

## Validations
- **Preview Endpoint**: `POST /api/v1.7/obligations/{obligation_id}/periods/{period_id}/payments/preview`
- **Preview Payload**: 
```json
{
  "amount": 20000,
  "source_account_id": "<uuid>"
}
```
- **Quote ID Generated**: Yes, now successfully generated and returned.
- **Payment Endpoint**: `POST /api/v1.7/obligations/{obligation_id}/periods/{period_id}/payments`
- **Payment Payload**:
```json
{
  "amount": 20000,
  "source_account_id": "<uuid>",
  "quote_id": "<uuid>"
}
```
- **Quote ID Sent in Payment**: Yes, frontend passes `quote_id` into the payment payload when available from the preview.

## QA Results
- **USD to COP preview QA**: Passed (mocked locally, requires VPS update for real test)
- **USD to COP payment QA**: Blocked pending VPS update, but structurally resolved since `quote_id` is now returned.
- **FIFO**: Still blocked until Steven confirms the fix resolves the manual QA.
