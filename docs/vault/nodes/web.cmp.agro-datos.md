---
id: web.cmp.agro-datos
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/agro-datos.tsx
---

# web/components/agro-datos

**Archivo:** `src/components/agro-datos.tsx`

## Qué hace
Pizarra editable de precios de la Cámara Arbitral de Cereales de Rosario (TRIGO, MAIZ, GIRASOL, SOJA, SORGO) en ARS y USD. Hace polling cada 10s, muestra quién y cuándo actualizó cada precio, y guarda ediciones con debounce de 800ms. Es la pestaña "Datos" del shell Agro.

Conecta con: pollea `GET /api/derivados-agro/camara` y persiste vía el mismo endpoint (service `camara_cereales.py`).

_Sin conexiones detectadas mecánicamente._
