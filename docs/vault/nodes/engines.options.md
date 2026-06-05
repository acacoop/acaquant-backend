---
id: engines.options
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines/options.py
---

# engines/options

> Motor de Opciones GGAL - Servicio Headless

**Archivo:** `engines/options.py`

## Qué hace
Motor headless de opciones de GGAL. Mantiene una sesión pyRofex propia (segunda conexión WS) y, en cada tick del book, calcula las griegas (delta/gamma/theta/vega), valor intrínseco e IV implícita con Black-Scholes, escribiendo el snapshot a Mongo con throttle de 300ms por símbolo. Es always-on en rueda (systemd `motor_options.service`).

Conecta con: escribe a `Opciones.Data` (último precio + griegas por símbolo); usa `quant.black_scholes` para las griegas/IV, `core.rofex_session` + `core.websocket` para el feed, y `core.mongo` para persistir. Lo consume el service `api.services.opciones` (chain de opciones) y el job de rollup `jobs.options_rollup`.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[core.rofex_session]]  ·  _module_
- [[core.websocket]]  ·  _module_
- [[quant.black_scholes]]  ·  _module_

## Lo usan (backlinks) ←
- [[svc.motor_options]]  ·  _service_
