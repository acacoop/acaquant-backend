---
id: core.notify
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core\notify.py
---

# core/notify

> Notificaciones operativas (Telegram).

**Archivo:** `core\notify.py`

## Qué hace
Canal de alertas operativas a Telegram, de una sola mano (el server avisa, Telegram nunca entra). `send_telegram()` postea texto al chat configurado; `notify_job_failure()` arma la alerta de un job caído con SOLO metadata (tipo, status, duración, último error truncado a 180 chars). Regla dura: nunca manda datos de clientes ni secretos. Si faltan `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` queda como no-op silencioso, y nunca propaga excepción (un fallo al notificar no debe tumbar el job).

Conecta con: la API de Telegram; lee `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` de `config.py`. Lo llaman `core.job_runs`, `jobs.watchdog` y `core.websocket` (agotamiento de reconexión). El detalle completo del incidente queda en `Manager.JobRuns`.

## Usa / conecta con →
- [[config]]  ·  _module_

## Lo usan (backlinks) ←
- [[core.job_runs]]  ·  _module_
- [[core.websocket]]  ·  _module_
- [[jobs.controles_datos]]  ·  _module_
- [[jobs.guardrails]]  ·  _module_
- [[jobs.ia_calidad]]  ·  _module_
- [[jobs.informe_salud]]  ·  _module_
- [[jobs.triage]]  ·  _module_
- [[jobs.watchdog]]  ·  _module_
