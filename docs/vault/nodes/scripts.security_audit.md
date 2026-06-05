---
id: scripts.security_audit
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/security_audit.py
---

# scripts/security_audit

> security_audit.py — chequeo de seguridad defensiva del repo (read-only).

**Archivo:** `scripts/security_audit.py`

## Qué hace
Herramienta reusable de seguridad defensiva (read-only) pensada para correr sin saber de seguridad: junta los chequeos automatizables y reporta en castellano priorizado 🔴/🟡/🟢. Cubre secretos en el working tree (claves/credenciales reales, descartando placeholders), deps con CVE (vía pip-audit si está), código inseguro (vía bandit si está) y postura de config (CORS abierto, debug/reload, logging de secretos). Lo que no puede chequear (allowlist de Atlas, puertos, SSH, secretos en el historial git) lo deja como checklist manual. Uso: `python -m scripts.security_audit`.

Conecta con: escanea el working tree del repo; invoca pip-audit y bandit si están instalados. No toca Mongo ni red propia.

_Sin conexiones detectadas mecánicamente._
