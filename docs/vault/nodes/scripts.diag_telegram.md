---
id: scripts.diag_telegram
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_telegram.py
---

# scripts/diag_telegram

> Diagnóstico del bot de Telegram — separa problema de TOKEN vs CHAT_ID.

**Archivo:** `scripts/diag_telegram.py`

## Qué hace
Diagnóstico one-shot del bot de Telegram: separa si el problema es el TOKEN o el CHAT_ID. Inspecciona el token cargado (enmascarado), llama getMe para validar solo el token, y si pasa prueba sendMessage para validar el chat. Sirve cuando las notificaciones operativas dejan de llegar (un 401 casi siempre es token mal copiado). Se corre con `python -m scripts.diag_telegram` en el Droplet.
Conecta con: lee config.TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID; valida el canal que usa core.notify para alertas.

## Usa / conecta con →
- [[config]]  ·  _module_
