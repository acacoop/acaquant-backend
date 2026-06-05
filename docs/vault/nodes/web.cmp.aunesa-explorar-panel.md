---
id: web.cmp.aunesa-explorar-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/aunesa-explorar-panel.tsx
---

# web/components/aunesa-explorar-panel

**Archivo:** `src/components/aunesa-explorar-panel.tsx`

## Qué hace
Panel exploratorio de MANAGER → AUNESA: trae los movimientos crudos del custodio Aunesa para una cuenta/fecha y los muestra parseados a boletos (op, ticker, cantidad, precio, importe, moneda, plazo) con sus líneas de detalle y la categoría/captura inferida. Sirve para diagnosticar cómo el backend interpreta la data del custodio.

Conecta con: pollea el endpoint exploratorio en vivo de `GET /api/manager/aunesa/...` (router `manager/aunesa.py`, cliente `core/aunesa.py`). Solo manager.

_Sin conexiones detectadas mecánicamente._
