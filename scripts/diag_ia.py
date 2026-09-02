"""`scripts/diag_ia.py` — ¿EL GATEWAY DE IA TIENE CREDENCIAL Y EL PROVEEDOR CONTESTA?

Read-only sobre nuestra base; hace UNA llamada mínima al proveedor (la tarea
`smoke`, 64 tokens). Es lo único que contesta las tres preguntas que el botón
«explicámelo» (AGENT.md §0.dh) necesita para funcionar:

    1. ¿hay credencial?        — `llm.configurado()` por proveedor
    2. ¿qué modelo se usaría?  — el tier flash de cada proveedor
    3. ¿responde?              — `ai.completar("smoke", …)`, con latencia

Uso:
    python -m scripts.diag_ia
"""
from __future__ import annotations

import sys
import time


def main() -> int:
    from core import ai, llm

    print("\n══ PROVEEDORES ══")
    alguno = False
    for p in llm.estado_proveedores():
        ok = p.get("configurado")
        alguno = alguno or bool(ok)
        print(f"  {p['proveedor']:<10} credencial: {'SÍ' if ok else 'no'} · "
              f"modelos: {p.get('modelos')} · saldo: {p.get('saldo')}")
    print(f"  default: {llm.PROVEEDOR_DEFAULT}")
    if not alguno:
        print("\n✗ Ningún proveedor tiene credencial en este .env: "
              "«explicámelo» va a decir «la IA no está configurada».")
        return 1

    print("\n══ SMOKE (una llamada mínima) ══")
    t0 = time.monotonic()
    texto = ai.completar("smoke", system="Contestá solo la palabra OK.", user="ping",
                         detalle="diag_ia")
    ms = int((time.monotonic() - t0) * 1000)
    if texto:
        print(f"  ✅ respondió en {ms} ms: {texto.strip()[:60]!r}")
        print("  «explicámelo» y cualquier tarea del gateway pueden funcionar.")
        return 0
    print(f"  ✗ no contestó ({ms} ms). Causas posibles, en orden: presupuesto diario "
          "agotado (ia.trazas lo dice), modelo mal nombrado en AI_MODEL_FLASH, "
          "proveedor caído. Mirar la última fila de ia.trazas.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
