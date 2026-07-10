# NEXUM V1.8 — HOTFIX OVERVIEW ENDPOINT 500 (SECOND RUNTIME ERROR)

## 1. Nuevo Stacktrace Real

Después de aplicar el hotfix que solucionaba el `NameError` de `Decimal`, un nuevo error surgió en producción:

```text
AttributeError: 'Obligation' object has no attribute 'day_of_month'
  File "/app/app/obligations/service_v17.py", line 259, in list_obligations_overview
    "day_of_month": ob.day_of_month,
```

Al resolver el `day_of_month`, la inspección de código determinó que existía otro posible fallo de atributo a continuación en la línea 265:
`"obligation_type": ob.obligation_type` debido a que el objeto `Obligation` del modelo de base de datos no tiene una propiedad `obligation_type`, sino `type` (`indefinite`, `one_time`, etc.).

## 2. Por qué el Hotfix de Decimal no fue suficiente

El error de `Decimal` detuvo la ejecución en la línea 171. Una vez resuelto, el código continuó hasta que llegó al momento de armar el payload de respuesta, donde se referenciaban atributos inexistentes (`start_date`, `day_of_month`, y luego `obligation_type`) que no estaban mapeados correctamente desde el modelo SQL sino que fueron construidos asumiendo erróneamente que las propiedades del esquema estaban en el modelo.

## 3. Root Cause

1. Pydantic `ObligationV17OverviewResponse` hereda de `ObligationV17Response`.
2. Las propiedades agregadas al final del diccionario devuelto por el servicio (`start_date`, `end_count`, `day_of_month`, etc.) ni siquiera estaban en el response model y, además, no existen en la clase `Obligation` del ORM.
3. El atributo `type` del modelo SQL debe mapearse manualmente a `obligation_type` del diccionario Pydantic, dado que no hay alias automático.

## 4. Fix Aplicado

**Backend:**
- Eliminadas las propiedades inexistentes del diccionario `overview.append({...})` (ej. `day_of_month`, `end_count`, etc.).
- Mapeado correcto de `obligation_type` basándose en el tipo del ORM: `"recurring" if ob.type == "indefinite" else "one_time"`.
- Mapeado correcto de defaults en campos obligatorios del frontend pero que pueden ser legacy null en BD:
  - `"amount_type": ob.amount_type or "variable"`
  - `"status": ob.status or "active"`

## 5. Pruebas y N+1
- Las pruebas han pasado.
- El problema del N+1 sigue resuelto (overview llama en batch).
- La vista devuelve defaults seguros si no existen los periodos.

## 6. Siguientes Pasos
- Backend listo para commitear.
- Requiere deploy del backend.
- Frontend no fue modificado en esta iteración.
