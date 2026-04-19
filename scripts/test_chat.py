"""test_chat.py — smoke test del endpoint /api/chat.

Requiere que uvicorn esté corriendo en localhost:8000. Probalo con:

    # terminal 1:
    uvicorn api.main:app --reload --port 8000

    # terminal 2:
    python -m scripts.test_chat
    python -m scripts.test_chat "qué contrapartes son fondos?"
    python -m scripts.test_chat --base http://api.acaquant.com "resumen del mercado"

Si la API exige Bearer token (API_KEY en .env), se lo pasa automático.
"""
import argparse
import json
import sys

import requests

from config import API_KEY

PREGUNTAS_DEFAULT = [
    "hola, qué podés hacer?",
    "listame las contrapartes que son fondos",
    "cómo están los breakevens hoy?",
]


def probar(base: str, pregunta: str, history: list | None = None) -> dict:
    url = f"{base.rstrip('/')}/api/chat"
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    body = {"message": pregunta}
    if history:
        body["history"] = history

    print(f"\n▶ {pregunta}")
    resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=90)
    if resp.status_code != 200:
        print(f"  ✗ HTTP {resp.status_code}: {resp.text[:400]}")
        return {}

    data = resp.json()
    print(f"  respuesta ({data['steps']} steps, {data['elapsed_s']}s):")
    print(f"    {data['reply']}")
    if data["tool_calls"]:
        print("  tools usadas:")
        for tc in data["tool_calls"]:
            mark = "✓" if tc["ok"] else "✗"
            print(f"    {mark} {tc['name']}({tc['args']})")
    usage = data.get("usage", {})
    if usage:
        print(
            f"  tokens: in={usage.get('promptTokenCount', '?')}, "
            f"out={usage.get('candidatesTokenCount', '?')}, "
            f"total={usage.get('totalTokenCount', '?')}"
        )
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("preguntas", nargs="*", help="Preguntas a probar (default: set predefinido).")
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="URL base de la API.")
    args = parser.parse_args()

    preguntas = args.preguntas or PREGUNTAS_DEFAULT
    for p in preguntas:
        probar(args.base, p)

    print("\n✓ test completo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
