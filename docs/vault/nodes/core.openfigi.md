---
id: core.openfigi
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/openfigi.py
---

# core/openfigi

> Cliente OpenFIGI con caching en Mongo (Smart.CusipCatalog).

**Archivo:** `core/openfigi.py`

## Qué hace
Cliente del servicio gratuito OpenFIGI (Bloomberg) que mapea identificadores financieros entre sí: CUSIP ↔ ISIN ↔ ticker ↔ FIGI con metadata (nombre, exchange, tipo). Se usa en dos sentidos: CUSIP→ticker al parsear 13Fs, y ticker→CUSIP al seedear CEDEARs. Cachea cada lookup en `Smart.CusipCatalog` (incluso los no-match, con ticker=null, para no reintentar) y respeta el rate-limit por ventana de 6s de OpenFIGI con headroom. Tiene lookup unitario y batch.

Conecta con: la API de OpenFIGI (`api.openfigi.com`); lee/escribe la colección `Smart.CusipCatalog` vía `core.mongo`. Lo invocan los flujos de Smart Money / seed de CEDEARs.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
