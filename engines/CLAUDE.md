# engines/ — contexto del subdirectorio

Motores WS → SQL, always-on L-V 13-20 UTC (los controla cron). El
`CLAUDE.md` raíz tiene lo project-wide. Incluye `motor_cedears` (alimenta
el Scanner CEDEARs).

## Patrón de escritura a `mercado.market_snapshot`

Dos motores escriben a `mercado.market_snapshot` (helper
`core/market_snapshot.py`) sin pisarse, vía el writer SQL-native de
`core.pg_mirror` (`write_native`):
- `engines/valores.py` (motor_rofex) → `book.bids/offers`,
  `metrics.{last_price, open_price, high_price, low_price, closing_price,
  vwap, total_nominals}`, `updated_at`. Refresh 1s.
- `engines/curvas.py` (motor_curvas) → `metrics.{TEA, TEM, duration,
  mod_duration, convexity, paridad}`. Refresh 5s.

Cada uno escribe SOLO sus campos (upsert parcial por ticker) — no pisa los
campos del otro motor. El registro no lleva `top_trades` ni `recent_trades`
(eran payload muerto).

## Motores stale

**Cuidado con deploys post-cierre**: motores que quedan corriendo sirven
snapshots stale al día siguiente. Triage rápido: `Active: since` en cada
motor (skill `/motor-status`).

## Reglas

- `python -m engines.<motor>` desde la raíz siempre (`python engines/x.py`
  falla — `core` no es discoverable).
- `engines/` usa `core/` + `quant/`. No importa de `api/`.
- Conexión SQL: `core.postgres.get_pool()` — nunca cerrarlo (mata el pool).
