"""Evaluación del flow estructurado de cartera contra el golden set.

Corre cada caso de `docs/asistente/golden_set_cartera.yaml` contra
`POST /api/chat/structured/cartera` y reporta pass/fail con criterios
booleanos sobre la respuesta.

Uso:
    python -m scripts.eval_cartera                # contra http://127.0.0.1:8000
    python -m scripts.eval_cartera --base-url https://api.acaquant.com

Salida:
    Tabla con [id, status (PASS/FAIL/ERROR), checks fallados, latencia, tokens].
    Exit code 0 si todos PASS, 1 si alguno FAIL.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests
import yaml

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
GOLDEN_PATH = Path(__file__).resolve().parent.parent / "docs" / "asistente" / "golden_set_cartera.yaml"


def load_cases() -> list[dict[str, Any]]:
    if not GOLDEN_PATH.exists():
        print(f"ERROR: golden set no encontrado en {GOLDEN_PATH}", file=sys.stderr)
        sys.exit(2)
    with GOLDEN_PATH.open() as f:
        data = yaml.safe_load(f) or {}
    return data.get("cases", []) or []


def run_case(base_url: str, case: dict[str, Any], api_key: str | None) -> dict[str, Any]:
    """Llama al endpoint con los params del caso. Devuelve dict con resultados."""
    url = f"{base_url.rstrip('/')}/api/chat/structured/cartera"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    t0 = time.time()
    try:
        resp = requests.post(url, json=case["request"], headers=headers, timeout=120)
    except requests.RequestException as e:
        return {"status": "ERROR", "error": str(e), "elapsed_s": round(time.time() - t0, 2)}

    elapsed = round(time.time() - t0, 2)
    if resp.status_code != 200:
        return {
            "status": "ERROR",
            "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            "elapsed_s": elapsed,
        }
    try:
        body = resp.json()
    except json.JSONDecodeError:
        return {"status": "ERROR", "error": "respuesta no-JSON", "elapsed_s": elapsed}

    return {
        "status": "OK",
        "body": body,
        "elapsed_s": elapsed,
    }


def evaluate(case: dict[str, Any], result: dict[str, Any]) -> tuple[bool, list[str]]:
    """Aplica los `expects` del caso al body. Devuelve (pass, lista_de_fallos)."""
    if result["status"] != "OK":
        return False, [result.get("error", "error desconocido")]

    body = result["body"]
    expects = case.get("expects", {}) or {}
    fallos: list[str] = []

    # ¿Hay data estructurada?
    data = body.get("data")
    if expects.get("tool_called") == "responder_cartera" and not data:
        fallos.append("modelo NO devolvió data estructurada (posible truncated o falla en tool_choice)")
        return False, fallos

    if data is None:
        # Sin data, ningún check de cartera puede pasar.
        fallos.append("data is None")
        return False, fallos

    cartera = data.get("cartera", []) or []

    min_items = expects.get("cartera_min_items")
    if min_items is not None and len(cartera) < min_items:
        fallos.append(f"cartera tiene {len(cartera)} items, esperado >= {min_items}")

    max_items = expects.get("cartera_max_items")
    if max_items is not None and len(cartera) > max_items:
        fallos.append(f"cartera tiene {len(cartera)} items, esperado <= {max_items}")

    if expects.get("pesos_suman_100"):
        if not body.get("pesos_ok", False):
            fallos.append(f"pesos suman {body.get('pesos_suma', '?')}% (esperado 100 ±0.5)")

    if expects.get("tiene_tesis") and not (data.get("tesis") or "").strip():
        fallos.append("tesis vacía")

    if expects.get("tiene_que_invalida") and not (data.get("que_invalida") or "").strip():
        fallos.append("que_invalida vacío")

    return len(fallos) == 0, fallos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="URL base del API")
    ap.add_argument("--api-key", default=None, help="Bearer token si el endpoint lo requiere")
    ap.add_argument("--only", default=None, help="Correr solo el caso con este id")
    args = ap.parse_args()

    cases = load_cases()
    if args.only:
        cases = [c for c in cases if c.get("id") == args.only]
        if not cases:
            print(f"No hay caso con id '{args.only}'", file=sys.stderr)
            return 2

    print(f"Corriendo {len(cases)} casos contra {args.base_url}")
    print("-" * 100)
    print(f"{'ID':<48} {'STATUS':<8} {'ELAPSED':>8}  CHECKS")
    print("-" * 100)

    all_pass = True
    for case in cases:
        case_id = case.get("id", "?")
        result = run_case(args.base_url, case, args.api_key)
        passed, fallos = evaluate(case, result)
        if not passed:
            all_pass = False
        status = "PASS" if passed else ("ERROR" if result["status"] != "OK" else "FAIL")
        elapsed = result.get("elapsed_s", 0)
        checks = "ok" if passed else " | ".join(fallos)
        print(f"{case_id:<48} {status:<8} {elapsed:>6}s  {checks}")

    print("-" * 100)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
