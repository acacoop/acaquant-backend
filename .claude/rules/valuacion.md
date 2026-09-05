---
paths:
  - "jobs/portafolio_backfill.py"
  - "api/services/pnl.py"
  - "api/services/pnl_sql.py"
  - "api/services/valuaciones_sql.py"
  - "api/services/titulos_flujos.py"
  - "jobs/assets_autofill.py"
---
# AuM, tenencias y valuación — fórmulas no inferibles

**AuM / tenencias** — fuente única SQL `portafolio.tenencia` (writer diario
`jobs/portafolio_backfill --diario`, 11:00 UTC L-V). Mongo `Valuaciones.AuM` fue
**ELIMINADA** (2026-06-15) junto con `jobs/aum.py::run` (queda solo de librería de
helpers Aunesa) y la tabla SQL `aum`. El divisor de la valuación lo decide la
**CARTERA** (no más `tipoTitulo`, que se quedaba NULL):
- Renta fija (cartera `HD / DL / ARS`, cotiza en paridad) → `cantidad × precio / 100`
- Cash (`MONEDAS`), `FCI`, `RENTA VARIABLE` → `cantidad × precio` (NUNCA ÷100)
- Futuros (`DERIVADOS`) → `(precio + 1) × cantidad`
- El motor de PnL (`pnl.py::_aplicar_normalizer`) usa la MISMA regla por cartera.

**AuM join chain**: `mercado.curvas` (campo `curva`) → `ticker` (era `ticker_corto`) → `portafolio.assets.ticker` → `unidad` → `portafolio.tenencia` (SQL, filtrar `aum='si'`).
