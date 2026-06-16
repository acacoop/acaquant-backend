---
id: svc.motor_portfolio_snapshot
type: service
layer: deploy
repo: infra
tags: [service, deploy, infra]
path: deploy\systemd\motor_portfolio_snapshot.service
---

# systemd: motor_portfolio_snapshot

> Servicio systemd.

**Archivo:** `deploy\systemd\motor_portfolio_snapshot.service`

## Qué hace
Servicio systemd del motor de snapshot de portfolio — corre `engines.portfolio_snapshot`, que captura el último precio de los tickers que están en tenencia de las cuentas (universo dinámico). Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/portfolio_snapshot.py`; arma el universo desde `engines/_universo_portfolio.py` y escribe el último precio a `Trading.PortfolioSnapshot`, usado para valuar tenencias. Controlado por cron.

## Usa / conecta con →
- [[engines.portfolio_snapshot]]  ·  _module_
