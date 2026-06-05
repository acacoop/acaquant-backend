---
id: scripts.perf_sweep
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/perf_sweep.py
---

# scripts/perf_sweep

> perf_sweep.py — Barrido de performance de los services (foto real de una).

**Archivo:** `scripts/perf_sweep.py`

## Qué hace
Herramienta reusable de profiling que corre EN EL DROPLET contra Atlas real una lista curada y solo-lectura de services que alimentan las vistas. Por cada uno mide cold_ms (primer hit, caché frío), warm_ms (segundo, muestra el ahorro de @cached) y el split %mongo (I/O) vs %cpu de la corrida fría — el dato que decide dónde atacar. Guarda el árbol de llamadas (HTML pyinstrument) de los más lentos en logs/. La lista de targets es un allowlist explícito de funciones de lectura, nunca escritura/órdenes. Se corre con `python -m scripts.perf_sweep [--top N] [--only <patrón>]`.

Conecta con: api.services.* (targets), core.mongo (read), pyinstrument; complementa scripts.profile_quant (CPU) y scripts.profile_motor (motores).

## Usa / conecta con →
- [[api.services]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[api.services.argy]]  ·  _module_
- [[api.services.back_office_titulos]]  ·  _module_
- [[api.services.camara_cereales]]  ·  _module_
- [[api.services.canje]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.comparar_inversion]]  ·  _module_
- [[api.services.derivados]]  ·  _module_
- [[api.services.derivados_agro]]  ·  _module_
- [[api.services.fair_value]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.mejoras_dispo]]  ·  _module_
- [[api.services.opciones]]  ·  _module_
- [[api.services.order_book]]  ·  _module_
- [[api.services.pnl]]  ·  _module_
- [[api.services.portfolio]]  ·  _module_
- [[api.services.rem]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.repo]]  ·  _module_
- [[api.services.scanner]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.Trading.Curvas]]  ·  _collection_
- [[db.Valuaciones.AuM]]  ·  _collection_
