"""debug_claude_key.py — diagnóstico completo de la ANTHROPIC_API_KEY.

Uso:
    /root/TradingAV/venv/bin/python -m scripts.debug_claude_key

Chequea:
1. Que la variable esté en .env.
2. Formato (prefijo, longitud, caracteres raros).
3. Hace una llamada real a Claude con Haiku y muestra el resultado.
"""
from __future__ import annotations

import json
import sys

import requests

from config import ANTHROPIC_API_KEY


def main() -> int:
    print("=" * 60)
    print("DIAGNÓSTICO ANTHROPIC_API_KEY")
    print("=" * 60)

    if not ANTHROPIC_API_KEY:
        print("✗ ANTHROPIC_API_KEY vacía o no configurada en .env")
        return 2

    key = ANTHROPIC_API_KEY
    print(f"\n1) Variable leída desde config.py:")
    print(f"   Longitud: {len(key)} caracteres")
    print(f"   Prefix:   {key[:15]}…")
    print(f"   Suffix:   …{key[-6:]}")

    # Chequeo caracteres invisibles
    stripped = key.strip()
    if stripped != key:
        print(f"   ⚠ tiene whitespace alrededor; la versión limpia tiene {len(stripped)} chars")
    for i, c in enumerate(key):
        if ord(c) < 32 or ord(c) == 127:
            print(f"   ✗ caracter invisible en posición {i}: ord={ord(c)}")
    if key.startswith("\"") or key.startswith("'"):
        print("   ✗ empieza con comilla — sacá las comillas del .env")
    if key.endswith("\"") or key.endswith("'"):
        print("   ✗ termina con comilla — sacá las comillas del .env")

    # Chequeo formato esperado
    print(f"\n2) Formato:")
    if key.startswith("sk-ant-api03-"):
        print("   ✓ prefix sk-ant-api03- correcto")
    elif key.startswith("sk-ant-"):
        print("   ⚠ prefix sk-ant- (sin api03) — puede ser de otro formato")
    else:
        print("   ✗ no empieza con sk-ant- → la key NO parece de Anthropic")
    if len(key) < 50:
        print(f"   ⚠ parece muy corta ({len(key)} chars); las keys nuevas son ~100-110 chars")

    # Llamada real
    print(f"\n3) Probando con la API real (modelo haiku):")
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            data=json.dumps({
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 50,
                "messages": [{"role": "user", "content": "decime 'ok' en una palabra"}],
            }),
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"   ✗ fallo de red: {e}")
        return 3

    print(f"   HTTP status: {resp.status_code}")
    try:
        body = resp.json()
    except Exception:
        print(f"   cuerpo (raw): {resp.text[:500]}")
        return 4

    if resp.status_code == 200:
        try:
            text = body["content"][0]["text"]
            usage = body.get("usage", {})
            print(f"   ✓ RESPUESTA OK: '{text}'")
            print(f"   tokens: in={usage.get('input_tokens')}, out={usage.get('output_tokens')}")
            print("\n✓ La key funciona perfecto. El problema (si lo hay) ya está resuelto.")
            return 0
        except (KeyError, IndexError):
            print(f"   ⚠ 200 pero respuesta rara: {json.dumps(body, indent=2)[:500]}")
            return 5

    # Errores típicos
    err = body.get("error", {})
    err_type = err.get("type", "")
    err_msg = err.get("message", "")
    print(f"   ✗ ERROR {err_type}: {err_msg}")

    if err_type == "authentication_error":
        print("\n→ La key es inválida. Opciones:")
        print("  - Se copió mal (chars faltantes / sobrantes).")
        print("  - Fue rotada o borrada en console.anthropic.com.")
        print("  - Es de otro workspace sin acceso al modelo.")
        print("  - Generá una nueva: console.anthropic.com → Settings → API Keys → Create Key.")
    elif err_type == "permission_error":
        print("\n→ La key no tiene permiso. Verificá que el proyecto tenga billing activo.")
    elif err_type == "not_found_error":
        print("\n→ Modelo no disponible para tu cuenta. Probá otro modelo o chequeá región.")

    return 1


if __name__ == "__main__":
    sys.exit(main())
