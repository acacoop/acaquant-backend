Motor live de dólar MEP/CCL/canje. Suscribe por WS los 3 tramos de AL30 (AL30 pesos, AL30D MEP, AL30C Cable) y en cada snapshot calcula MEP = AL30_offer/AL30D_bid, CCL = AL30_offer/AL30C_bid y el canje entre ambos. Es la fuente intradiaria; el histórico lo escribe el cron `engines.dolar_mep`.

Conecta con: escribe `Valuaciones.DolarSnapshot` (1 doc, replaced cada 5s); usa `core.rofex_session` + `core.websocket`. Lo invoca systemd `motor_dolares.service`. El endpoint `/api/cotizaciones/mep` lee de este snapshot primero (live), con fallback a `Valuaciones.Dolar`.
