---
id: engines._curvas_loader
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines\_curvas_loader.py
---

# engines/_curvas_loader

> Carga común del master de renta fija para todos los motores.

**Archivo:** `engines\_curvas_loader.py`

## Qué hace
Loader común que centraliza la lectura de `Trading.Curvas` (definición estática de instrumentos) para todos los motores de renta fija. Unifica lo que antes eran 4 funciones de carga casi idénticas: expone `cargar_todos`, `cargar_por_curva` (agrupado por campo `curva`), `cargar_indexado_por_ticker` y `cargar_tickers_ordenados`. Usa el singleton de Mongo directamente (los motores ya lo tienen inicializado).

Conecta con: lee `Trading.Curvas` vía `core.mongo`. Lo consumen `engines.curvas` (indexado por ticker), `engines.forwards` y `engines.breakevens` (agrupado por curva) y `engines.valores` (tickers ordenados).

## Usa / conecta con →
- [[config]]  ·  _module_
- [[core]]  ·  _module_
- [[core.adhoc_subscriptions]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_

## Lo usan (backlinks) ←
- [[engines.breakevens]]  ·  _module_
- [[engines.curvas]]  ·  _module_
- [[engines.forwards]]  ·  _module_
- [[engines.valores]]  ·  _module_
