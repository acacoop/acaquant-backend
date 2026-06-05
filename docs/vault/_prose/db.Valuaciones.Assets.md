Catálogo de activos valuables en la base `Valuaciones`: cada `TICKER` con su `unidad` y tipo. Es el eslabón que conecta los instrumentos de mercado con las tenencias del AuM (un instrumento sin doc acá no aparece en portfolios/AuM).

Conecta con: une `Trading.Curvas.ticker_corto` → `Assets.TICKER` → `AuM.unidad` (cadena del AuM). La leen `api/services/portfolio.py`, `valuaciones.py`, `pnl.py`, `engines/_universo_portfolio.py`; se administra vía `api/routers/manager/assets.py`.
