# engines/ — contexto del subdirectorio

Motores WS → Mongo, always-on L-V 13-20 UTC (los controla cron). El
`CLAUDE.md` raíz tiene lo project-wide. Incluye `motor_cedears` (alimenta
el Scanner CEDEARs).

## Patrón de escritura a `Trading.MarketSnapshot`

Dos motores escriben a `MarketSnapshot` con `$set` parcial sin pisarse:
- `engines/valores.py` (motor_rofex) → `book.bids/offers`,
  `metrics.{last_price, open_price, high_price, low_price, closing_price,
  vwap, total_nominals}`, `updated_at`. Refresh 1s.
- `engines/curvas.py` (motor_curvas) → `metrics.{TEA, TEM, duration,
  mod_duration, convexity, paridad}`. Refresh 5s.

Cada uno escribe SOLO sus campos via `UpdateOne($set: dot-notation,
upsert=True)`. **No usar `ReplaceOne`** — pisa los campos del otro motor.
El doc no tiene `top_trades` ni `recent_trades` (eran payload muerto).

## Atlas pausado / motores stale

Atlas pausado 04:00–11:20 UTC diario (ahorro). Durante la ventana la API
devuelve error de conexión — no es bug. **Cuidado con deploys post-cierre**:
motores que quedan corriendo a través de la pausa sirven snapshots stale al
día siguiente. Triage rápido: `Active: since` en cada motor (skill
`/motor-status`).

## Reglas

- `python -m engines.<motor>` desde la raíz siempre (`python engines/x.py`
  falla — `core` no es discoverable).
- `engines/` usa `core/` + `quant/`. No importa de `api/`.
- Nunca `client.close()` sobre los Mongo singletons — mata el pool.
