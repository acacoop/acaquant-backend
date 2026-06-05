---
id: api.routers.ingest
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/ingest.py
---

# api/routers/ingest

> Ingesta (escritura) — datos que ENTRAN desde fuera del Droplet.

**Archivo:** `api/routers/ingest.py`

## Qué hace
Router HTTP `/api/ingest/dolar-oficial` — puerta de ENTRADA de datos desde fuera del Droplet. La PC de oficina pollea el dólar mayorista de MAE y, en vez de escribir Mongo directo, postea acá; el Droplet (cuya IP sí está whitelisteada en Atlas) los persiste. Así Atlas se cierra a la IP del Droplet (adiós 0.0.0.0/0). Doble auth: CF Access + un `X-Ingest-Token` dedicado (fail-closed: sin token configurado nadie escribe).

Conecta con: `core.dolar_oficial::upsert_oficial` que escribe en `Valuaciones.DolarOficialLive`; valida `config.DOLAR_INGEST_TOKEN`. Lo invoca el script `mae_forex` de la PC de oficina. Ver docs/SECURITY.md.

## Usa / conecta con →
- [[config]]  ·  _module_
- [[core.dolar_oficial]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
