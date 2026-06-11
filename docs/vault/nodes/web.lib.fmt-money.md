---
id: web.lib.fmt-money
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src/lib/fmt-money.ts
---

# web/lib/fmt-money

**Archivo:** `src/lib/fmt-money.ts`

## Qué hace
Helpers de formato de montos para el módulo Comercial. `fmtMoney` devuelve el monto compacto con sufijo de magnitud en locale es-AR (k = mil, M = millón, MM = mil millones, B = billón; ej. "$12,6 B", "$50 k"), y `fmtMoneyFull` el monto completo con separador de miles para tooltips. `null`/NaN → "—".

Conecta con: utilidad pura de presentación, sin I/O; la consumen los componentes del Tablero Comercial del frontend para mostrar AuM y volúmenes que vienen de `api.services.comercial`.

## Lo usan (backlinks) ←
- [[web.cmp.acreencias-view]]  ·  _component_
- [[web.cmp.coberturas-view]]  ·  _component_
- [[web.cmp.cobros-futuros-view]]  ·  _component_
- [[web.cmp.comercial-informe-view]]  ·  _component_
- [[web.cmp.comercial-operaciones-view]]  ·  _component_
- [[web.cmp.metricas-panel]]  ·  _component_
- [[web.cmp.referidos-view]]  ·  _component_
- [[web.cmp.trade-lab-view]]  ·  _component_
