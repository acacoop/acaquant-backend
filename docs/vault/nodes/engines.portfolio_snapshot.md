---
id: engines.portfolio_snapshot
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines/portfolio_snapshot.py
---

# engines/portfolio_snapshot

> Motor dedicado a captura del último precio para tickers de tenencia.

**Archivo:** `engines/portfolio_snapshot.py`

## Qué hace
Motor dedicado a tener el `last_price`/`closing_price` live de SOLO los tickers que la mesa tiene en posición hoy. Sesión pyRofex propia y suscripción mínima (entries LAST + CLOSING_PRICE, sin book), bulk_write cada 1s con dirty-flag. Un thread cada 60 min recalcula el universo de tenencia y agrega tickers nuevos sin reabrir el WS.

Conecta con: escribe a `Trading.PortfolioSnapshot` (last/closing por ticker) y audita cada refresh en `Manager.PortfolioSnapshotLog`; arma su universo vía `engines._universo_portfolio` (lee `Valuaciones.AuM` + `CashFlow.NegocioMovimientos`). Lo invoca systemd `motor_portfolio_snapshot.service`. Alimenta la valuación live de carteras (`api.services.portfolio`).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[core.rofex_session]]  ·  _module_
- [[core.websocket]]  ·  _module_
- [[engines._universo_portfolio]]  ·  _module_

## Lo usan (backlinks) ←
- [[svc.motor_portfolio_snapshot]]  ·  _service_
