"""Prueba el canal de alertas de Telegram end-to-end.

Manda un mensaje de prueba al chat configurado (TELEGRAM_BOT_TOKEN +
TELEGRAM_CHAT_ID). Sirve para confirmar que el bot quedó bien configurado
ANTES de depender de él para alertas reales.

Uso (en el Droplet, con las env vars seteadas):
    python -m scripts.test_alerta
"""
from __future__ import annotations

from datetime import UTC, datetime

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.notify import send_telegram


def main() -> int:
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN vacío — seteá la env var primero.")
        return 1
    if not TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_CHAT_ID vacío — seteá la env var primero.")
        return 1

    ahora = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    ok = send_telegram(f"✅ TradingAV: alerta de prueba ({ahora}). Si ves esto, el canal funciona.")
    if ok:
        print("✅ Mensaje enviado. Revisá el chat de Telegram.")
        return 0
    print("❌ No se pudo enviar. Revisá token/chat_id y la conexión.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
