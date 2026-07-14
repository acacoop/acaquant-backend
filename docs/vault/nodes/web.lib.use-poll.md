---
id: web.lib.use-poll
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src\lib\use-poll.ts
---

# web/lib/use-poll

**Archivo:** `src\lib\use-poll.ts`

## Qué hace
Hook `usePoll` — refresca data de un endpoint cada N milisegundos manteniendo el `initial` (SSR) como fallback y exponiendo `lastAt` (epoch ms del último fetch exitoso, en hora del browser) para el indicador de "última actualización". Reemplaza el viejo AutoRefresh global. Resetea solo cuando cambia el endpoint (no por recreación del objeto `initial`), evitando saltos a data vieja. Conserva la data anterior si un poll puntual falla.

Conecta con: hook de cliente; hace `fetch` con `cache: "no-store"` a route handlers del frontend que a su vez proxean al backend; lo usan las vistas live (mercado, dólares, órdenes) para mantenerse vivas sin recargar.

## Lo usan (backlinks) ←
- [[web.cmp.agro-datos]]  ·  _component_
- [[web.cmp.agro-mejoras-dispo]]  ·  _component_
- [[web.cmp.derivados-agro-futuros]]  ·  _component_
- [[web.cmp.derivados-agro-opciones]]  ·  _component_
- [[web.cmp.derivados-agro-pizarra]]  ·  _component_
- [[web.cmp.derivados-sinteticos-view]]  ·  _component_
- [[web.cmp.derivados-view]]  ·  _component_
- [[web.cmp.fair-value-view]]  ·  _component_
- [[web.cmp.forwards-panel]]  ·  _component_
- [[web.cmp.ons-live]]  ·  _component_
- [[web.cmp.renta-fija-live]]  ·  _component_
- [[web.cmp.scanner-view]]  ·  _component_
- [[web.cmp.titulos-mercado-view]]  ·  _component_
- [[web.cmp.trading-movers-scanner]]  ·  _component_
- [[web.cmp.trading-pivot-radar]]  ·  _component_
- [[web.cmp.trading-renta-fija-scanner]]  ·  _component_
- [[web.cmp.trading-view]]  ·  _component_
- [[web.cmp.trading-volumen-scanner]]  ·  _component_
