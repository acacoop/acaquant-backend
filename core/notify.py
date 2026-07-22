"""Notificaciones operativas (Telegram).

Vía de salida principal: el server le manda mensajes a Telegram. Pensado para
alertas de jobs/incidentes.

⚠ AMPLIACIÓN 2026-07-22 (buzón de pedidos): el server ahora también LEE de
Telegram, para poder aprobar un pedido con un tap. La postura de seguridad se
mantiene, y el cómo importa:

- **Sigue sin haber puerta de entrada.** Se usa `getUpdates` (consulta
  SALIENTE del server), no un webhook: Telegram nunca inicia una conexión y no
  se abre ningún puerto. Si el server está caído, no pasa nada.
- **Lo que llega no es un comando, es un voto.** Lo único que un update puede
  producir es el cambio de estado de UN pedido a aceptado/descartado. No hay
  texto libre interpretado, no hay shell, no hay SQL armado con el contenido.
- **Lista blanca explícita** (`TELEGRAM_ADMIN_IDS`) + chat fijo. Vacía = nadie
  aprueba desde Telegram. Default-deny.
- La regla vieja sigue en pie: las alertas llevan METADATA, jamás datos de
  clientes.

REGLA: las alertas llevan METADATA operativa (qué se rompió), NUNCA datos
de clientes (nombres, cuentas, posiciones, montos) ni secretos. El detalle
completo queda en `manager.job_runs`, privado; la alerta solo avisa.

Config (env, ver config.py): TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (+
TELEGRAM_ADMIN_IDS para aprobar). Si falta token/chat queda deshabilitado
(no-op silencioso). Nunca lanza excepción — un fallo al notificar no debe
tumbar el job que lo llamó.
"""
from __future__ import annotations

import logging

from config import TELEGRAM_ADMIN_IDS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

_TIMEOUT_S = 5
_MAX_LEN = 3500  # Telegram corta a 4096; dejamos margen.


def _api(metodo: str, payload: dict, *, timeout: int = _TIMEOUT_S) -> dict | None:
    """Una llamada a la API del bot. None si no hay credencial o falló — nunca
    propaga excepción (mismo contrato que el resto del módulo)."""
    if not TELEGRAM_BOT_TOKEN:
        return None
    try:
        import requests

        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{metodo}",
                             json=payload, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("Telegram %s HTTP %s: %s", metodo, resp.status_code,
                           resp.text[:200])
            return None
        return resp.json()
    except Exception as e:
        logger.warning("Telegram %s falló: %s", metodo, e)
        return None


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


# ── Aprobación con botones (buzón de pedidos) ────────────────────────────────
#
# Ver la nota de seguridad del encabezado: esto NO abre una puerta al server.
# `botones` es una lista de filas, cada fila una lista de (texto, dato). El
# `dato` vuelve tal cual cuando alguien toca el botón — se mantiene corto y
# estructurado ("<accion>:<id>"), nunca texto libre.

def send_telegram_botones(text: str, botones: list[list[tuple[str, str]]],
                          *, markdown: bool = True) -> bool:
    """Mensaje con teclado inline. True si se envió."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return False
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text[:_MAX_LEN],
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": [
            [{"text": t, "callback_data": d[:64]} for t, d in fila] for fila in botones
        ]},
    }
    if markdown:
        payload["parse_mode"] = "Markdown"
    return _api("sendMessage", payload) is not None


def leer_taps(offset: int | None = None) -> tuple[list[dict], int | None]:
    """Lee los TAPS de botones pendientes. Devuelve (taps, nuevo_offset).

    Cada tap: {callback_id, dato, quien, autorizado, mensaje_id}. El filtro de
    autorización se resuelve ACÁ (chat fijo + lista blanca de ids) para que
    ningún caller pueda olvidárselo: si `autorizado` es False, el caller debe
    limitarse a responderle que no puede.

    Solo mira `callback_query` — los mensajes de texto que le manden al bot se
    IGNORAN por completo. Menos superficie: no hay comandos que interpretar."""
    r = _api("getUpdates", {"timeout": 0, "allowed_updates": ["callback_query"],
                            **({"offset": offset} if offset is not None else {})},
             timeout=10)
    if not r or not r.get("ok"):
        return [], offset
    taps, ultimo = [], offset
    for up in r.get("result") or []:
        ultimo = int(up["update_id"]) + 1
        cq = up.get("callback_query") or {}
        if not cq:
            continue
        quien = str((cq.get("from") or {}).get("id") or "")
        chat = str(((cq.get("message") or {}).get("chat") or {}).get("id") or "")
        taps.append({
            "callback_id": cq.get("id"),
            "dato": str(cq.get("data") or ""),
            "quien": quien,
            "autorizado": bool(quien and quien in TELEGRAM_ADMIN_IDS
                               and chat == str(TELEGRAM_CHAT_ID)),
            "mensaje_id": (cq.get("message") or {}).get("message_id"),
        })
    return taps, ultimo


def responder_tap(callback_id: str, texto: str) -> bool:
    """Confirma el tap (Telegram muestra un aviso arriba y saca el reloj del
    botón). Sin esto el botón queda 'cargando' para siempre."""
    return _api("answerCallbackQuery",
                {"callback_query_id": callback_id, "text": texto[:200]}) is not None
