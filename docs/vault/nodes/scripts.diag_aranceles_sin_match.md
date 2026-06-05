---
id: scripts.diag_aranceles_sin_match
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_aranceles_sin_match.py
---

# scripts/diag_aranceles_sin_match

> diag_aranceles_sin_match.py — inspecciona boletos de Aunesa /informes que NO

**Archivo:** `scripts/diag_aranceles_sin_match.py`

## Qué hace
Diagnóstico de solo lectura que inspecciona boletos del endpoint Aunesa /operaciones/informes que NO están en CashFlow.NegocioMovimientos: toma cuentas (al azar o las indicadas), pide informes, cruza por boleto==comprobante y reporta los faltantes. Doble propósito: entender los "sin match" del backfill de aranceles (hipótesis: agro/mercados aún no ingestados) e inventariar tipoOperacion/naturaleza para inferir el campo `mercado`. No escribe. Se corre `python -m scripts.diag_aranceles_sin_match [--n-cuentas 40] [--cuenta 805]`.
Conecta con: CashFlow.NegocioMovimientos (lee), core.aunesa (informes del custodio), core.mongo (read).

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.aunesa]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
