"""Diagnóstico del bot de Telegram — separa problema de TOKEN vs CHAT_ID.

Un 401 en sendMessage casi siempre es el token mal copiado. Este script:
  1. Inspecciona el token cargado (longitud, formato, espacios/comillas),
     enmascarado para no exponerlo entero.
  2. Llama getMe → valida SOLO el token. Si acá da 401, el token está mal.
  3. Si el token es válido, prueba sendMessage → ahí se ve si el problema
     es el chat_id (400 chat not found) o que no iniciaste el chat (403).

Uso (en el Droplet, con las env vars seteadas):
    python -m scripts.diag_telegram
"""
from __future__ import annotations

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def _mask(tok: str) -> str:
    if len(tok) <= 12:
        return "(demasiado corto)"
    return f"{tok[:8]}…{tok[-4:]}"


def main() -> int:
    import requests

    tok = TELEGRAM_BOT_TOKEN
    chat = TELEGRAM_CHAT_ID

    print("── Inspección del token cargado ──")
    print(f"  presente:        {bool(tok)}")
    print(f"  longitud:        {len(tok)} (lo normal ronda 46)")
    print(f"  enmascarado:     {_mask(tok)}")
    print(f"  tiene ':' :      {':' in tok}  (el token siempre tiene un dos-puntos)")
    print(f"  espacios extra:  {tok != tok.strip()}  (debería ser False)")
    print(f"  comillas pegadas:{tok[:1] in chr(34) + chr(39) or tok[-1:] in chr(34) + chr(39)}  (debería ser False)")
    print(f"  chat_id:         {chat!r}")
    if not tok or not chat:
        print("\n❌ Falta token o chat_id en el .env.")
        return 1

    print("\n── getMe (valida SOLO el token) ──")
    try:
        r = requests.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=8)
    except Exception as e:
        print(f"❌ Error de red: {e}")
        return 1
    if r.status_code == 401:
        print("❌ 401 Unauthorized → el TOKEN está mal. Recopialo de @BotFather:")
        print("   /mybots → tu bot → API Token. Pegalo en .env sin espacios ni comillas.")
        return 1
    if r.status_code != 200:
        print(f"❌ getMe devolvió {r.status_code}: {r.text[:200]}")
        return 1
    bot = r.json().get("result", {})
    print(f"✅ Token OK. Bot: @{bot.get('username')} ({bot.get('first_name')})")

    print("\n── sendMessage (valida el chat_id) ──")
    r2 = requests.post(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        json={"chat_id": chat, "text": "✅ diag_telegram: token y chat_id OK."},
        timeout=8,
    )
    if r2.status_code == 200:
        print("✅ Mensaje enviado. Revisá Telegram. TODO OK.")
        return 0
    print(f"❌ sendMessage {r2.status_code}: {r2.text[:200]}")
    print("   400 'chat not found' → el chat_id está mal.")
    print("   403 'bot can't initiate' → abrí el bot en Telegram y tocá Start.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
