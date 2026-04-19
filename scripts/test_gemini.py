"""test_gemini.py — smoke test de la API de Gemini (Flash y Pro).

Uso:
    python -m scripts.test_gemini
    python -m scripts.test_gemini --modelo flash
    python -m scripts.test_gemini --modelo pro
    python -m scripts.test_gemini --prompt "tu pregunta"

Lee GEMINI_API_KEY desde config.py (que carga .env).
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

from config import GEMINI_API_KEY

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

MODELOS = {
    "flash": "gemini-2.5-flash",
    "pro":   "gemini-2.5-pro",
}

PROMPT_DEFAULT = "Decime en una frase qué es el CER argentino."


def probar_modelo(nombre_corto: str, prompt: str) -> bool:
    modelo = MODELOS[nombre_corto]
    url = ENDPOINT.format(model=modelo, key=GEMINI_API_KEY)
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"\n▶ {modelo}")
    print(f"  prompt: {prompt}")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
        except Exception:
            err = {"raw": str(e)}
        print(f"  ✗ HTTP {e.code}: {json.dumps(err, indent=2, ensure_ascii=False)}")
        return False
    except Exception as e:
        print(f"  ✗ error: {e}")
        return False

    elapsed = time.time() - t0
    try:
        texto = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        usage = data.get("usageMetadata", {})
        in_tok = usage.get("promptTokenCount", "?")
        out_tok = usage.get("candidatesTokenCount", "?")
        print(f"  ✓ {elapsed:.2f}s · in={in_tok} tok · out={out_tok} tok")
        print(f"  respuesta: {texto}")
        return True
    except (KeyError, IndexError) as e:
        print(f"  ✗ respuesta inesperada ({e}): {json.dumps(data, indent=2, ensure_ascii=False)}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--modelo",
        choices=["flash", "pro", "todos"],
        default="todos",
        help="Cuál modelo probar (default: todos)",
    )
    parser.add_argument(
        "--prompt",
        default=PROMPT_DEFAULT,
        help=f"Prompt a enviar (default: '{PROMPT_DEFAULT}')",
    )
    args = parser.parse_args()

    if not GEMINI_API_KEY:
        print("✗ GEMINI_API_KEY no está definida en .env")
        return 2

    print(f"GEMINI_API_KEY: {GEMINI_API_KEY[:10]}…{GEMINI_API_KEY[-4:]}")

    modelos_a_probar = ["flash", "pro"] if args.modelo == "todos" else [args.modelo]
    resultados = [probar_modelo(m, args.prompt) for m in modelos_a_probar]

    print()
    if all(resultados):
        print("✓ todos los modelos respondieron OK")
        return 0
    print("✗ alguno falló, ver detalles arriba")
    return 1


if __name__ == "__main__":
    sys.exit(main())
