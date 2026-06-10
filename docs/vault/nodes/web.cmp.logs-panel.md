---
id: web.cmp.logs-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/logs-panel.tsx
---

# web/components/logs-panel

**Archivo:** `src/components/logs-panel.tsx`

## Qué hace
Visor de logs systemd en vivo: dropdown con los servicios (motores de mercado, api, cloudflared), refresca cada 3s y colorea las líneas por prioridad (error/warn/info/debug). Configurable cuántas líneas mostrar.

Conecta con: fetch a `/api/manager/logs?servicio=X` (lee journalctl del servicio en el Droplet). Se monta como tab dentro de `manager-view`.

## Usa / conecta con →
- [[api.routers.manager.logs]]  ·  _module_
