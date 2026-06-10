---
id: engines.valores
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines/valores.py
---

# engines/valores

**Archivo:** `engines/valores.py`

## Qué hace
Motor de precios principal (motor_rofex). Suscribe por WS pyRofex todos los tickers de `Trading.Curvas` (más extras de config), mantiene el order book y la microestructura en memoria y, cada 1s, vuelca el estado completo de cada ticker a Mongo con un único bulk_write. Además persiste cada trade individual al histórico de operaciones.

Conecta con: escribe `book.bids/offers` y `metrics.{last_price,open,high,low,closing,vwap,total_nominals}` a `Trading.MarketSnapshot` (vía `$set` parcial, sin pisar las métricas de `engines.curvas`) y agrega trades a `Trading.TimeSales`; toma el universo de `engines._curvas_loader`, usa `core.rofex_session` + `core.websocket`. Lo invoca systemd `motor_rofex.service`. Es la fuente base de precios que consumen casi todos los services de renta fija/variable y el resto de los motores derivados.

## Usa / conecta con →
- [[core.adhoc_subscriptions]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[core.rofex_session]]  ·  _module_
- [[core.websocket]]  ·  _module_
- [[db.Trading.MarketSnapshot]]  ·  _collection_
- [[db.Trading.TimeSales]]  ·  _collection_
- [[engines._curvas_loader]]  ·  _module_

## Lo usan (backlinks) ←
- [[svc.motor_rofex]]  ·  _service_
