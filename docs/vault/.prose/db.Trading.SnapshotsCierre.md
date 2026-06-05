Cierre diario por bono en la base `Trading`: persiste precio y métricas de cierre por instrumento, post-cierre de mercado. Es el histórico de cierres que evita recomputar desde trades y sirve para PnL, carry y descomposición.

Conecta con: la escribe el cron `jobs/snapshot_cierre.py` (lee `MarketSnapshot` y materializa el cierre); la leen `api/services/pnl.py`, `carry_trade.py`, `renta_fija.py`, `analitica.py` y `jobs/consolidado_cuentas.py`.
