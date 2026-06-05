---
id: scripts.mae_forex_client
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/mae_forex_client.py
---

# scripts/mae_forex_client

> mae_forex_client.py — feed de dólar oficial PARA TU NOTEBOOK (Anaconda).

**Archivo:** `scripts/mae_forex_client.py`

## Qué hace
Cliente del feed de dólar oficial mayorista que corre EN LA NOTEBOOK de la oficina (no en el Droplet, que MAE rechaza por IP). Pollea MAE cada 30s y, en vez de escribir directo a Mongo, hace POST a /api/ingest/dolar-oficial: así se puede cerrar el 0.0.0.0/0 de Atlas. Manda solo el mayorista UST$T/M/000, que es el único que el backend lee. Trae interruptores DRY_RUN y RUN_ONCE para probar sin enviar. Se corre con `python scripts/mae_forex_client.py` (config inline, sin commitear keys reales).

Conecta con: endpoint api.routers.ingest (POST /api/ingest/dolar-oficial), Cloudflare Access (service token), Valuaciones.DolarOficialLive (destino final vía Droplet).

_Sin conexiones detectadas mecánicamente._
