"""Notificaciones operativas (Telegram).

Vía de salida de UNA mano: el server le manda mensajes a Telegram; Telegram
NUNCA entra al server. Pensado para alertas de jobs/incidentes.

REGLA: las alertas llevan METADATA operativa (qué se rompió), NUNCA datos
de clientes (nombres, cuentas, posiciones, montos) ni secretos. El detalle
completo queda en Mongo (Manager.JobRuns), privado; la alerta solo avisa.

Config (env, ver config.py): TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID. Si
falta cualquiera, queda deshabilitado (no-op silencioso). Nunca lanza
excepción — un fallo al notificar no debe tumbar el job que lo llamó.
"""
from __future__ import annotations

import logging

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

_TIMEOUT_S = 5
_MAX_LEN = 3500  # Telegram corta a 4096; dejamos margen.


def send_telegram(text: str, *, markdown: bool = True) -> bool:
    """Manda un mensaje al grupo/chat configurado. True si se envió.

    No-op (False) si no hay token/chat. Nunca propaga excepción.
    `markdown=False` manda texto plano (útil cuando el contenido tiene `_`/`*`
    que el parser de Markdown se comería — ej. nombres de jobs como sync_postgres).
    """
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return False
    try:
        import requests

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text[:_MAX_LEN],
            "disable_web_page_preview": True,
        }
        if markdown:
            payload["parse_mode"] = "Markdown"
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=_TIMEOUT_S,
        )
        if resp.status_code != 200:
            logger.warning("Telegram alert HTTP %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as e:
        logger.warning("Telegram alert falló: %s", e)
        return False


def notify_job_failure(
    tipo: str,
    status: str,
    *,
    elapsed_s: float | None = None,
    n_errors: int = 0,
    last_error: str | None = None,
) -> bool:
    """Alerta de job fallido — SOLO metadata. El último error se trunca a
    180 chars (defensa por si arrastra un dato); el detalle completo vive
    en Manager.JobRuns."""
    icon = "🔴" if status == "error" else "🟠"
    lines = [f"{icon} Job `{tipo}` → *{status}*"]
    if elapsed_s is not None:
        lines.append(f"⏱ {elapsed_s:.1f}s")
    if n_errors:
        lines.append(f"errores: {n_errors}")
    if last_error:
        snippet = last_error.replace("\n", " ")[:180]
        lines.append(f"último: `{snippet}`")
    lines.append("_detalle en Manager.JobRuns_")
    return send_telegram("\n".join(lines))
