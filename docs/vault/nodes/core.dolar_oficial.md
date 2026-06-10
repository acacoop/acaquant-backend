---
id: core.dolar_oficial
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/dolar_oficial.py
---

# core/dolar_oficial

> Fuente única para el "dólar oficial" mayorista.

**Archivo:** `core/dolar_oficial.py`

## Qué hace
Fuente única del "dólar oficial" mayorista (A3500). Lee el último precio + variación del ticker MAE UST$T desde `Valuaciones.DolarOficialLive`, que escribe un script local (`mae_forex.py`) corriendo en una PC de oficina porque la IP del Droplet quedó bloqueada en MAE. Sin fallback retail: si la PC está caída, devuelve None y el front muestra "—". También expone el `upsert_oficial` que persiste lo que llega.

Conecta con: lee/escribe `Valuaciones.DolarOficialLive`; el upsert lo alimenta el endpoint `POST /api/ingest/dolar-oficial`; lo consumen services y vistas que muestran el mayorista.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.DolarOficialLive]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.ingest]]  ·  _module_
- [[api.services.argy]]  ·  _module_
- [[api.services.derivados_agro]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.mejoras_dispo]]  ·  _module_
- [[api.services.sinteticos]]  ·  _module_
- [[engines.curvas]]  ·  _module_
- [[engines.futuros_dlr]]  ·  _module_
