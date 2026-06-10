---
id: core.rofex_session
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/rofex_session.py
---

# core/rofex_session

**Archivo:** `core/rofex_session.py`

## Qué hace
Inicialización básica de la sesión pyRofex en entorno LIVE para los motores de market data: setea URL/WS y llama a `pyRofex.initialize()` con las credenciales de `Config`. Es la sesión "de lectura de mercado", distinta de `core.rofex_orders_session` (que es para operar).

Conecta con: la plataforma ROFEX/Primary vía pyRofex; lee `Config.USER/PASSWORD/ACCOUNT/URL/WS` de `config.py`. La levantan los motores de mercado (`engines/*`, ej. `motor_rofex`) antes de abrir el WebSocket con `core.websocket`.

## Usa / conecta con →
- [[config]]  ·  _module_

## Lo usan (backlinks) ←
- [[engines.caucion]]  ·  _module_
- [[engines.dolar_mep]]  ·  _module_
- [[engines.dolares]]  ·  _module_
- [[engines.futuros_dlr]]  ·  _module_
- [[engines.motor_agro]]  ·  _module_
- [[engines.motor_agro_opciones]]  ·  _module_
- [[engines.motor_cedears]]  ·  _module_
- [[engines.options]]  ·  _module_
- [[engines.portfolio_snapshot]]  ·  _module_
- [[engines.valores]]  ·  _module_
