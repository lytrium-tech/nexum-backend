# Nexum V1.5 — Pay Early Principal Constraint Hotfix Deploy Report

## Resumen de Despliegue
Se ha desplegado a producción (`https://api.nexum.lytrium.tech`) el hotfix backend para corregir el error de restricción de base de datos en pago anticipado de tarjeta de crédito (500).

## Identificadores de Commit
- **Local commit:** `28cd4b1`
- **VPS commit:** `28cd4b1`

## Estado de Producción
- **Migraciones requeridas:** no
- **Migraciones ejecutadas:** no (No ejecutar migraciones)
- **Docker status:** healthy
- **Health:** ok
- **Readiness:** ok

## Validación (Runtime Smoke)
Dado que no se cuenta con un token de QA seguro para interactuar con datos reales en el entorno de producción, la validación se limitó a:
- Confirmar el hash del commit actual en la VPS (`28cd4b1`).
- Comprobar que los contenedores docker de la API estén ejecutándose y en estado saludable.
- Verificar que las rutas `/health` y `/health/readiness` respondan exitosamente (`status: ok`).

## Archivos modificados en el commit desplegado
- `app/credit/service.py`
- `tests/unit/test_multicurrency_qa.py`
- `docs/agent/reports/nexum-v1-5-payearly-production-500-real-trace.md`

## Notas de Integración
El fix corrige un comportamiento de base de datos al realizar pagos que liquidan el total del principal restante de una compra con cuotas futuras. No afecta los contratos de la API ni requiere adaptaciones en el frontend.
- Se requiere retest de Runtime QA.
