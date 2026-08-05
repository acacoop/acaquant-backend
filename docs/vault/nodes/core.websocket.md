---
id: core.websocket
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/websocket.py
---

# core/websocket

> WebSocketManager — conexión WS a pyRofex para los motores de mercado.

**Archivo:** `core/websocket.py`

## Qué hace
`WebSocketManager` — la conexión WebSocket a pyRofex compartida por TODOS los motores de mercado. Abre el socket, suscribe tickers en lotes de 50 (con profundidad y entries configurables), traduce cada mensaje de Rofex y se lo pasa al MarketManager del motor (`update_price`). Maneja resiliencia: ante corte, reconecta en background con backoff (6 intentos) y, solo si se agota, alerta a Telegram throttleado 1×/30min por motor (los cortes transitorios van solo al log).

Conecta con: la plataforma ROFEX vía pyRofex (sesión de `core.rofex_session`); empuja precios al MarketManager de cada `engines/*`; alerta vía `core.notify`. Un bug acá rompe todos los motores a la vez.

## Usa / conecta con →
- [[core.simbolos_cuarentena]]  ·  _module_
- [[core.threads]]  ·  _module_

## Lo usan (backlinks) ←
- [[engines._motor_base]]  ·  _module_
- [[engines.motor_cedears]]  ·  _module_
- [[engines.options]]  ·  _module_
- [[engines.portfolio_snapshot]]  ·  _module_
- [[engines.valores]]  ·  _module_
