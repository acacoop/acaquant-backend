---
id: scripts.discovery_pyrofex
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/discovery_pyrofex.py
---

# scripts/discovery_pyrofex

> Discovery one-shot de pyRofex.get_detailed_instruments().

**Archivo:** `scripts/discovery_pyrofex.py`

## Qué hace
Discovery one-shot de pyRofex: lista todos los instrumentos que expone ROFEX (futuros, opciones, spreads, ETFs) y los agrupa por código CFI, con count, underlyings únicos y samples. Sirve para identificar qué CFI usar antes de extender un motor a productos nuevos (agro, opciones) sin filtrar a ciegas. Persiste idempotente a Manager.PyRofexDiscovery con _id="current". Requiere sesión pyRofex. Se corre con `python -m scripts.discovery_pyrofex`.
Conecta con: usa core.rofex_session; escribe Manager.PyRofexDiscovery (consultada por api.routers.manager.checks).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[core.rofex_session]]  ·  _module_
