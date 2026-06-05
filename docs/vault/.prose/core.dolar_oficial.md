Fuente única del "dólar oficial" mayorista (A3500). Lee el último precio + variación del ticker MAE UST$T desde `Valuaciones.DolarOficialLive`, que escribe un script local (`mae_forex.py`) corriendo en una PC de oficina porque la IP del Droplet quedó bloqueada en MAE. Sin fallback retail: si la PC está caída, devuelve None y el front muestra "—". También expone el `upsert_oficial` que persiste lo que llega.

Conecta con: lee/escribe `Valuaciones.DolarOficialLive`; el upsert lo alimenta el endpoint `POST /api/ingest/dolar-oficial`; lo consumen services y vistas que muestran el mayorista.
