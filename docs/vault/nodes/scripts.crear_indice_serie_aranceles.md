---
id: scripts.crear_indice_serie_aranceles
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/crear_indice_serie_aranceles.py
---

# scripts/crear_indice_serie_aranceles

> Crea un índice que CUBRE la serie de /ops/aranceles → consulta sin FETCH.

**Archivo:** `scripts/crear_indice_serie_aranceles.py`

## Qué hace
Crea un índice "covered" en CashFlow.Operaciones que cubre la serie del endpoint /ops/aranceles (moneda, segmento, concertacion, tipo_operacion, arancel), para que Mongo resuelva match + group + sum dentro del índice sin abrir documentos. Diagnóstico previo mostró que la serie hacía FETCH de cientos de miles de docs (~1-1,8s de primer load) por faltarle `arancel` y `tipo_operacion`. Idempotente (no recrea si ya existe). Se corre `python -m scripts.crear_indice_serie_aranceles` y luego se verifica con scripts.diag_perf_aranceles.
Conecta con: CashFlow.Operaciones (índice), endpoint /ops/aranceles, api.services.operaciones_informes.ensure_indexes (si se vuelve permanente), core.mongo.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
