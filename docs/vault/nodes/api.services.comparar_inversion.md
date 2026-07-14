---
id: api.services.comparar_inversion
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\comparar_inversion.py
---

# api/services/comparar_inversion

> Comparar Inversión — service que compara 2 bonos de Trading.Curvas lado a lado.

**Archivo:** `api\services\comparar_inversion.py`

## Qué hace
Compara dos bonos de `Trading.Curvas` lado a lado para el tab "Comparar Inversión". Fase 1: universo restringido a curvas con flujos modelados (cer, tasa_fija, soberanos); tamar y dolar_linked quedan fuera hasta tener calendario. Reusa metadata + métricas live, el calendario de flujos por 100 VN y el MEP para conversión cross-moneda ARS↔USD. Fase 2 (pendiente): sumar `Trading.BondsMaster`.

Conecta con: lee `Trading.Curvas` y `MarketSnapshot.metrics` vía `api.services.renta_fija` (`listar_curva`, `_calendario_flujos`, `_bonos_cer_fijados`); usa `get_ultimo_mep`; lo consume el endpoint de comparar inversión en `/retorno`.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[core]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_
- [[engines.curvas]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.analitica]]  ·  _module_
- [[api.services.copiloto]]  ·  _module_
