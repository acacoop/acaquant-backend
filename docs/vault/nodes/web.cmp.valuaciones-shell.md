---
id: web.cmp.valuaciones-shell
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/valuaciones-shell.tsx
---

# web/components/valuaciones-shell

**Archivo:** `src/components/valuaciones-shell.tsx`

## Qué hace
Shell (contenedor con sub-tabs) de la pantalla Valuaciones. Tres sub-vistas: PORTAFOLIO (serie/historia por cuenta), PnL Títulos y Totales (todas las cuentas). Incluye el combobox de selección de cuenta y persiste la sub-tab y la cuenta elegida en la URL para no perder la posición al refrescar.

Conecta con: carga la lista de cuentas y orquesta valuaciones-view, pnl-titulos-view y pnl-totales-view (que pegan a los endpoints de valuaciones/PnL). Reusa el CuentaCombobox de aum-view.

## Usa / conecta con →
- [[web.cmp.aum-view]]  ·  _component_
- [[web.cmp.pnl-titulos-view]]  ·  _component_
- [[web.cmp.pnl-totales-view]]  ·  _component_
- [[web.cmp.valuaciones-view]]  ·  _component_

## Lo usan (backlinks) ←
- [[web.view.valuaciones.view]]  ·  _view_
