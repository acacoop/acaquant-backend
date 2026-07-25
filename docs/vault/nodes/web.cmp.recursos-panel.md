---
id: web.cmp.recursos-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/recursos-panel.tsx
---

# web/components/recursos-panel

**Archivo:** `src/components/recursos-panel.tsx`

## Qué hace
Panel de monitoreo de recursos del servidor (Droplet): CPU, memoria, swap, disco, load average y uptime del sistema, más el consumo (RSS/CPU) por proceso de cada motor/servicio. Grafica la historia reciente con áreas y poltea cada 60s. Es una vista de salud para el Manager.

Conecta con: pega al endpoint de recursos del servidor (api.routers.manager_resources), que muestrea el host con psutil. Solo para roles con acceso a Manager.

## Usa / conecta con →
- [[api.routers.manager]]  ·  _module_
