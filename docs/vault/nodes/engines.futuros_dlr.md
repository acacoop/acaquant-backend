---
id: engines.futuros_dlr
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines/futuros_dlr.py
---

# engines/futuros_dlr

> Motor de futuros DLR (Dólar A3500) — outrights single-leg.

**Archivo:** `engines/futuros_dlr.py`

## Qué hace
Motor de futuros de dólar (DLR / Dólar A3500). Descubre dinámicamente los outrights single-leg vigentes (cficode FXXXSX, excluye spreads y variantes "M"), los suscribe por WS y calcula la tasa implícita TNA lineal de cada vencimiento contra el spot mayorista. Al apagado (cierre 20:05 UTC) vuelca un cierre diario como serie histórica.

Conecta con: escribe `Trading.FuturosDLRSnapshot` (live, replaced cada 15s) y `Trading.FuturosDLR` (cierre por fecha+ticker); toma el spot vía `core.dolar_oficial` (MAE `Valuaciones.DolarOficialLive` → fallback `Trading.DOLAR` → `Valuaciones.Dolar.mep`); usa `core.rofex_session` + `core.websocket`. Lo invoca systemd `motor_futuros_dlr.service`. Lo consume `api.services.derivados` (futuros/sintéticos DLR).

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.dolar_oficial]]  ·  _module_
- [[core.dolar_sql]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_
- [[core.series_macro]]  ·  _module_
- [[db.Trading.DOLAR]]  ·  _collection_
- [[engines._motor_base]]  ·  _module_

## Lo usan (backlinks) ←
- [[svc.motor_futuros_dlr]]  ·  _service_
