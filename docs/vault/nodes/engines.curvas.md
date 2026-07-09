---
id: engines.curvas
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines/curvas.py
---

# engines/curvas

> main_curvas.py — Motor de enriquecimiento en tiempo real para Trading.TimeSales.

**Archivo:** `engines/curvas.py`

## Qué hace
Motor de enriquecimiento de curva (motor_curvas). Cada 2s recalcula, para cada bono de `Trading.Curvas`, las métricas analíticas (TEA, TEM, duration, mod_duration, convexidad, paridad) según el tipo de instrumento (CER/tasa fija/soberano) usando el último precio. Recarga CER cada 1h, MEP cada 1 min y el dólar mayorista A3500 cada 5 min para mantener paridad/TEA pegadas al spot.

Conecta con: escribe SOLO los campos `metrics.{TEA,TEM,duration,...}` a `Trading.MarketSnapshot` (vía `$set` parcial, sin pisar los del motor de precios); lee la definición de instrumentos vía `engines._curvas_loader` (`Trading.Curvas`), CER de `Trading.CER`/BCRA y MEP/A3500 vía `core.dolar_oficial`. Lo invoca systemd `motor_curvas.service`. Las TEA que escribe las consumen `engines.forwards` y `engines.breakevens`, además de los services de renta fija.

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.calendario]]  ·  _module_
- [[core.dolar_oficial]]  ·  _module_
- [[core.dolar_sql]]  ·  _module_
- [[core.market_snapshot]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_
- [[core.series_macro]]  ·  _module_
- [[engines._curvas_loader]]  ·  _module_
- [[quant.xirr]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager.checks]]  ·  _module_
- [[api.services.acreencias]]  ·  _module_
- [[api.services.comparar_inversion]]  ·  _module_
- [[api.services.debug_curva]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.renta_fija_sql]]  ·  _module_
- [[api.services.sensibilidad]]  ·  _module_
- [[engines.breakevens]]  ·  _module_
- [[jobs.backfill_tasas]]  ·  _module_
- [[svc.motor_curvas]]  ·  _service_
