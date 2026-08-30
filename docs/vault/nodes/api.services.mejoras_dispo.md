---
id: api.services.mejoras_dispo
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/mejoras_dispo.py
---

# api/services/mejoras_dispo

> Service — Mejoras Precio Disponible (Agro).

**Archivo:** `api/services/mejoras_dispo.py`

## Qué hace
Replica la planilla de "Mejoras Precio Disponible" del Agro: en vez de pagarle al productor en pesos hoy, le propone colocar en una LECAP/BONCAP a X días y, si cubre con un futuro DLR del mismo mes, dolarizar el resultado. Calcula tasa directa, interés ganado, valor final en ARS y su equivalente en USD por commodity (SOJA/MAIZ/TRIGO). Cache 5s.

Conecta con: lee el precio ARS manual de `Derivados.CamaraCereales`, la TNA (TIR efectiva) de `Trading.MarketSnapshot.metrics.TEA` y el dólar oficial live (`core.dolar_oficial`); matchea LECAP↔futuro DLR por año-mes igual que `sinteticos`. Lo expone el router `/api/derivados/agro`.

## Usa / conecta con →
- [[quant.tasas]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.services.agro_sql]]  ·  _module_
