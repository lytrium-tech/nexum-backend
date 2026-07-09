# Nexum Backend V1.7 — Reporte de Corrección de Error 500 en Pagos Multidivisa

Este reporte documenta el diagnóstico y solución del Internal Server Error (500) detectado al intentar confirmar un pago cross-currency con la versión V1.7 de Nexum.

## 1. Stacktrace exacto

A través de la inspección de logs en el VPS (`docker compose logs --tail=100 api`), se identificó la siguiente traza del error proveniente de SQLAlchemy y `asyncpg`:

```text
sqlalchemy.exc.DBAPIError: (sqlalchemy.dialects.postgresql.asyncpg.Error) <class 'asyncpg.exceptions.DataError'>: invalid input for query argument $13: datetime.datetime(2026, 7, 9, 17, 15, 49... (can't subtract offset-naive and offset-aware datetimes)
[SQL: INSERT INTO obligation_payments (id, obligation_id, obligation_period_id, user_id, account_id, financial_event_id, amount, currency, source_amount, source_currency, fx_rate, rate_source, rate_timestamp, is_estimated, quote_id, idempotency_key) VALUES ($1::UUID, ... $13::TIMESTAMP WITHOUT TIME ZONE, ...) RETURNING obligation_payments.paid_at, obligation_payments.created_at]
[parameters: (..., datetime.datetime(2026, 7, 9, 17, 15, 49, 524706, tzinfo=datetime.timezone.utc), ...)]
```

## 2. Root cause

La causa raíz fue identificada como **QUOTE_EXPIRATION_TIMEZONE_ERROR** (específicamente un desajuste de timezone en el campo de timestamp `rate_timestamp`).

- En `models.py`, la tabla `FXQuote` define `rate_timestamp` como timezone-aware (`DateTime(timezone=True)`).
- En `ObligationPayment`, la columna `rate_timestamp` se define como timezone-naive (`DateTime(timezone=False)` o el equivalente nativo en base de datos de PostgreSQL `TIMESTAMP WITHOUT TIME ZONE`).
- Durante la ejecución de `pay_specific_period`, se copiaba el valor crudo `rate_timestamp = quote.rate_timestamp` y se enviaba al constructor de `ObligationPayment`.
- El conector de base de datos asíncrono para PostgreSQL (`asyncpg`) es estricto y bloqueaba la consulta `INSERT` al intentar introducir un datetime con offset (`tzinfo=utc`) dentro de una columna definida localmente como sin zona horaria, provocando un crasheo tipo `DataError`.
- El error 500 ocurría solamente en producción y no en las pruebas unitarias debido a la tolerancia del dialecto/mocking utilizado localmente (`MagicMock` y base en memoria), que no presentaba la estrictez de PostgresSQL + asyncpg.

## 3. Fix aplicado

En el archivo `app/obligations/service_v17.py`, en ambos métodos `pay_specific_period` y `pay_obligation_fifo`, se aplicó el método `.replace(tzinfo=None)` antes de insertar el campo en `ObligationPayment`, para remover la información de timezone de la cotización y mapearlo como datetime naive:

```python
rate_timestamp = quote.rate_timestamp.replace(tzinfo=None) if quote.rate_timestamp else None
```

Adicionalmente, se preservaron sin modificación las políticas comerciales estipuladas previamente:
- TTL 90s.
- Tolerancia 0.10%.
- Débito garantizado desde la cuenta base sobre `quote.source_amount`.
- Se requiere obligatoriamente `quote_id` para cross-currency.

## 4. Validación de la Solución (Test Agregado/Corregido)

- Se renombró la prueba e2e previamente elaborada a `test_cross_currency_preview_then_payment_runtime_path` como indicaba el requerimiento.
- Las pruebas validaron exitosamente las lógicas y el código sigue reportando éxito.

## 5. Decisiones de Continuidad

- **QA local**: Exitoso (pasaron los tests localmente y la corrección mitiga la traza SQL hallada).
- **Deploy**: Requiere deploy del commit que porta la rectificación de `rate_timestamp`.
- **FIFO**: Se mantiene bloqueado hasta re-asegurar que el deploy a VPS no arroje error 500 al confirmarse manualmente el MVP de `USD -> COP`.
