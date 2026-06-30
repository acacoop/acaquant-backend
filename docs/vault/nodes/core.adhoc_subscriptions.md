---
id: core.adhoc_subscriptions
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/adhoc_subscriptions.py
---

# core/adhoc_subscriptions

> Helpers para mercado.adhoc_subscriptions — suscripciones live efímeras.

**Archivo:** `core/adhoc_subscriptions.py`

## Qué hace
Maneja suscripciones live efímeras a tickers que el usuario pidió desde el Dashboard de Operar pero que no están ni en `Trading.Curvas` ni en `config.TICKERS_EXTRA_PRECIOS`. Hace upsert con TTL rodante de 7 días (cada poll refresca el vencimiento) y un cap global de 50 suscripciones activas (rechaza con 429 al pasarse).

El motor de mercado pollea esta colección cada 5s para saber qué suscribir vía pyRofex y persistir en `MarketSnapshot` como cualquier otro ticker. Mongo borra solo los vencidos por TTL.

Conecta con: escribe/lee `Trading.AdhocSubscriptions`; lo invocan el endpoint de ingesta del Dashboard de Operar (`subscribe`/`bump_last_used`) y los motores (`list_active_tickers`).

## Usa / conecta con →
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.operar]]  ·  _module_
- [[engines._curvas_loader]]  ·  _module_
- [[engines.valores]]  ·  _module_
