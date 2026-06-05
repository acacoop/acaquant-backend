Vista de PnL por título de una cuenta: tabla por ticker con cantidad, precio promedio, costo remanente, valor actual (live/cierre/AuM), PnL realizado / no realizado / pasivo y total, con espejo en USD (costo a MEP histórico, valor a MEP de hoy). Cada fila se puede desplegar al detalle de boletos que componen la posición. Exporta tipos y formateadores que reusa pnl-totales-view.

Conecta con: pega al endpoint de PnL por cuenta (api.routers.carteras/valuaciones → api.services.pnl). El motor de PnL lee CashFlow/AuM y el cache Valuaciones.PnLTotalesCache para el espejo USD.
