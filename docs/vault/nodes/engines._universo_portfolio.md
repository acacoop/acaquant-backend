---
id: engines._universo_portfolio
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines\_universo_portfolio.py
---

# engines/_universo_portfolio

> Universo dinámico para motor_portfolio_snapshot.

**Archivo:** `engines\_universo_portfolio.py`

## Qué hace
Helper read-only que arma el universo dinámico de tickers para `motor_portfolio_snapshot`: el set de symbols pyRofex (`MERV - XMEV - X - 24hs`) a suscribir para tener last_price live. Combina los instrumentos con tenencia hoy (último snapshot de AuM, qty≠0) con los tickers operados hoy, y valida cada candidato contra la lista canónica de instrumentos.

Conecta con: lee `Valuaciones.AuM`, `Valuaciones.Assets`, `CashFlow.NegocioMovimientos` y valida contra `Manager.PyRofexInstruments` (todo vía `core.mongo` read-only). Su única función `tickers_de_tenencia()` la consume `engines.portfolio_snapshot`.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[core.postgres]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_

## Lo usan (backlinks) ←
- [[engines.portfolio_snapshot]]  ·  _module_
