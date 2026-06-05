Panel "Chart & Retornos" del cuadrante inferior derecho del Scanner. Dos tabs: CHART (gráfico de TradingView embebido, bloqueado al ticker seleccionado) y RETORNOS DIARIOS (histograma SVG de los retornos del último año, ~252 puntos, marcando el retorno de hoy y la media/σ). Re-monta al cambiar el ticker; los retornos se piden lazy.

Conecta con: pega a /api/scanner/returns/{ticker} (api.routers.scanner → api.services.scanner sobre Trading.PreciosAcciones). Embebe el widget tradingview-chart. Lo usa scanner-view.
