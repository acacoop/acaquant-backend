Motor de caución a corto plazo en pesos y dólares. Suscribe por WS los 2 tickers de caución cuyo plazo coincide con "días al próximo día hábil" (lun-jue 1D, vie 3D, etc.), re-evaluando el plazo cada hora y re-suscribiéndose si cambia. Persiste la TNA live y, al apagado (cierre 20:05 UTC), vuelca un cierre diario como serie histórica.

Conecta con: escribe `Trading.CaucionSnapshot` (live, ReplaceOne cada 15s) y `Trading.Caucion` (cierre por fecha+moneda); usa `core.rofex_session` + `core.websocket`. Lo invoca systemd `motor_caucion.service`. Lo consume el mercado repo vía `api.services.repo`.
