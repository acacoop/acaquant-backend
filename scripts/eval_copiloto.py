"""eval_copiloto.py — corre el eval set del copiloto (QuantAI Fase 0.4).

Ejecuta cada caso de evals/copiloto_vista.json contra el copiloto REAL
(datos vivos + DeepSeek — gasta tokens, ~22k por caso) y chequea las regex
esperadas/prohibidas. Es el candado de regresión del prompt: se corre DESPUÉS
de cada cambio a copiloto.py (prompt, columnas, reglas) y ANTES de darlo por
bueno. Un caso que falla = el cambio rompió algo que ya funcionaba.

Los outputs de un LLM varían entre corridas → los checks son de PRESENCIA
(matcheó al menos una esperada / ninguna prohibida), no de igualdad exacta.

Uso (Droplet):
    python -m scripts.eval_copiloto              # todos los casos
    python -m scripts.eval_copiloto --caso zona_pp_anual
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from api.services import copiloto

CASOS_PATH = Path(__file__).resolve().parent.parent / "evals" / "copiloto_vista.json"
USUARIO_EVAL = "eval@copiloto"  # usuario propio → su gasto se ve aparte en ia.trazas


def correr_caso(vista: str, caso: dict) -> tuple[bool, list[str]]:
    out = copiloto.preguntar(vista, caso["pregunta"], usuario=USUARIO_EVAL)
    if not out.get("ok"):
        return False, [f"el copiloto degradó: {out.get('error')}"]
    resp = out["respuesta"]
    problemas: list[str] = []
    esperado = caso.get("esperado_any") or []
    if esperado and not any(re.search(p, resp) for p in esperado):
        problemas.append(f"ninguna esperada matcheó ({esperado})")
    for p in caso.get("prohibido") or []:
        if re.search(p, resp):
            problemas.append(f"apareció prohibida: {p!r}")
    max_chars = caso.get("max_chars")
    if max_chars and len(resp) > max_chars:
        problemas.append(f"respuesta larga: {len(resp)} chars (máx {max_chars}) — "
                         "¿se derramó el razonamiento?")
    return not problemas, problemas + [f"respuesta: {resp[:300]}"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--caso", help="correr solo este id")
    args = ap.parse_args()

    spec = json.loads(CASOS_PATH.read_text())
    casos = [c for c in spec["casos"] if not args.caso or c["id"] == args.caso]
    if not casos:
        raise SystemExit(f"caso {args.caso!r} no existe en {CASOS_PATH.name}")

    fallados = 0
    for c in casos:
        ok, detalle = correr_caso(spec["vista"], c)
        print(f"{'✓ PASS' if ok else '✗ FAIL'}  {c['id']}  ({c['origen']})")
        if not ok:
            fallados += 1
            for linea in detalle:
                print(f"        {linea}")
        time.sleep(1)

    print(f"\n{len(casos) - fallados}/{len(casos)} casos OK")
    if fallados:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
