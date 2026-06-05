---
id: web.lib.use-poll
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src/lib/use-poll.ts
---

# web/lib/use-poll

**Archivo:** `src/lib/use-poll.ts`

## Qué hace
Hook `usePoll` — refresca data de un endpoint cada N milisegundos manteniendo el `initial` (SSR) como fallback y exponiendo `lastAt` (epoch ms del último fetch exitoso, en hora del browser) para el indicador de "última actualización". Reemplaza el viejo AutoRefresh global. Resetea solo cuando cambia el endpoint (no por recreación del objeto `initial`), evitando saltos a data vieja. Conserva la data anterior si un poll puntual falla.

Conecta con: hook de cliente; hace `fetch` con `cache: "no-store"` a route handlers del frontend que a su vez proxean al backend; lo usan las vistas live (mercado, dólares, órdenes) para mantenerse vivas sin recargar.

_Sin conexiones detectadas mecánicamente._
