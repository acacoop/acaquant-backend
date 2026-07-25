---
id: web.lib.api
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src/lib/api.ts
---

# web/lib/api

**Archivo:** `src/lib/api.ts`

## Qué hace
Cliente HTTP server-side del frontend para pegarle a la API de TradingAV (`api.acaquant.com`). Su función `apiFetch` arma cada request con el Bearer (`API_KEY`), el service token de Cloudflare Access (CF-Access-Client-Id/Secret) y, sobre todo, propaga la identidad del usuario (`x-acaquant-user-email` + `cf-access-authenticated-user-email`) para que el backend aplique RBAC. Maneja timeout (15s default), revalidate de Next y extrae el `detail` de los errores de FastAPI.

Conecta con: lee la identidad verificada vía `web.lib.cf-access` (`trustedEmail`) y `next/headers`; le pega a todos los endpoints del backend (`api.main`) desde SSR y route handlers; sin el email propagado el backend cae en `service:*` → DEFAULT_ROLE.

## Lo usan (backlinks) ←
- [[web.api.api.argy]]  ·  _route_
- [[web.api.api.aum-diff]]  ·  _route_
- [[web.api.api.aum-fci.serie]]  ·  _route_
- [[web.api.api.aum-fci.snapshot]]  ·  _route_
- [[web.api.api.aum-pnl]]  ·  _route_
- [[web.api.api.aum-pnl-todas]]  ·  _route_
- [[web.api.api.aum-total.serie]]  ·  _route_
- [[web.api.api.aum-total.snapshot]]  ·  _route_
- [[web.api.api.back-office.acreencias.[...path]]]  ·  _route_
- [[web.api.api.back-office.tenencia-hd.[[...path]]]]  ·  _route_
- [[web.api.api.back-office.tesoreria.[[...path]]]]  ·  _route_
- [[web.api.api.back-office.titulos-mercado]]  ·  _route_
- [[web.api.api.cashflow]]  ·  _route_
- [[web.api.api.caucion]]  ·  _route_
- [[web.api.api.comparar]]  ·  _route_
- [[web.api.api.comparar.bonos]]  ·  _route_
- [[web.api.api.contrapartes]]  ·  _route_
- [[web.api.api.derivados-agro]]  ·  _route_
- [[web.api.api.derivados-agro.camara]]  ·  _route_
- [[web.api.api.derivados-agro.camara.[cereal]]]  ·  _route_
- [[web.api.api.derivados-agro.costo-pase]]  ·  _route_
- [[web.api.api.derivados-agro.descuento-caucion]]  ·  _route_
- [[web.api.api.derivados-agro.dolares-referencia]]  ·  _route_
- [[web.api.api.derivados-agro.estrategia.simular]]  ·  _route_
- [[web.api.api.derivados-agro.mejoras-dispo]]  ·  _route_
- [[web.api.api.derivados-agro.opciones.[commodity]]]  ·  _route_
- [[web.api.api.derivados-agro.pizarra.[commodity]]]  ·  _route_
- [[web.api.api.derivados-agro.tasas-cobertura]]  ·  _route_
- [[web.api.api.derivados-sinteticos]]  ·  _route_
- [[web.api.api.dolares-historico]]  ·  _route_
- [[web.api.api.futuros-dlr]]  ·  _route_
- [[web.api.api.historico-curva]]  ·  _route_
- [[web.api.api.market.calendar]]  ·  _route_
- [[web.api.api.market.quotes]]  ·  _route_
- [[web.api.api.mep]]  ·  _route_
- [[web.api.api.news]]  ·  _route_
- [[web.api.api.news.article]]  ·  _route_
- [[web.api.api.opciones-meta]]  ·  _route_
- [[web.api.api.portfolio-cuentas]]  ·  _route_
- [[web.api.api.portfolio.niveles-1]]  ·  _route_
- [[web.api.api.portfolio.operadores]]  ·  _route_
- [[web.api.api.research.[...path]]]  ·  _route_
- [[web.api.api.scanner.[...path]]]  ·  _route_
- [[web.api.api.trades]]  ·  _route_
- [[web.api.api.trading.[...path]]]  ·  _route_
- [[web.api.api.valuaciones.[id_cuenta].mensual]]  ·  _route_
- [[web.api.api.valuaciones.[id_cuenta].movimientos]]  ·  _route_
- [[web.api.api.valuaciones.[id_cuenta].posiciones]]  ·  _route_
- [[web.api.api.valuaciones.[id_cuenta].posiciones-actuales]]  ·  _route_
- [[web.api.api.valuaciones.[id_cuenta].serie]]  ·  _route_
- [[web.api.api.valuaciones.[id_cuenta].variacion]]  ·  _route_
- [[web.api.api.valuaciones.consolidado]]  ·  _route_
- [[web.view.agro.view]]  ·  _view_
- [[web.view.derivados.view]]  ·  _view_
- [[web.view.ons.view]]  ·  _view_
- [[web.view.renta-fija.view]]  ·  _view_
- [[web.view.renta-variable.view]]  ·  _view_
- [[web.view.research.view]]  ·  _view_
