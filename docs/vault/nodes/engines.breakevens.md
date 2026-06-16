---
id: engines.breakevens
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines\breakevens.py
---

# engines/breakevens

> main_breakevens.py — Motor de breakevens CER/Lecap en tiempo real.

**Archivo:** `engines\breakevens.py`

## Qué hace
Motor de breakevens de inflación CER/Lecap en tiempo real. Cada 30s empareja cada bono tasa fija (Lecap/Boncap) con el bono CER de vencimiento más cercano y calcula la inflación mensual implícita que pricea el mercado entre hoy y ese vto. Cada par trae el `mes_inflacion` (IPC al que refiere, por el rezago del CER).

Conecta con: lee TEM de Lecap y paridad de CER vía `engines._curvas_loader` (`Trading.Curvas`) + el calendario de liquidación CER que reusa de `engines.curvas`; escribe `Trading.BreakevensLive` (live) y `Trading.BreakevensHistorico` (1 doc por fecha). Lo invoca systemd `motor_breakevens.service`. Lo consume `api.services.derivados`; backfill histórico por `jobs.backfill_breakevens`.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Trading.MarketSnapshot]]  ·  _collection_
- [[db.Trading.TimeSales]]  ·  _collection_
- [[engines._curvas_loader]]  ·  _module_
- [[engines.curvas]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager.checks]]  ·  _module_
- [[jobs.backfill_breakevens]]  ·  _module_
- [[svc.motor_breakevens]]  ·  _service_
