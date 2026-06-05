Versión agregada del PnL para TODAS las cuentas (no una sola): proxea con el parámetro `filtro_cuenta` (default "todas") al backend, que lo sirve desde la cache precalculada. Sin cache de edge.

Conecta con: vista de PnL consolidado del front → este route → backend `GET /api/portfolio/pnl-todas` (alimentado por `Valuaciones.PnLTotalesCache`, job `jobs/pnl_totales_precompute.py`).
