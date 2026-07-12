"""bateria_home.py — batería de 15 preguntas al copiloto de HOME.

Corre contra el copiloto REAL (datos vivos + DeepSeek) para mapear el pulso
segmentado nuevo (renta fija por curva/tramo, acciones, dólares/tasas,
commodities) y cazar bloqueos de verificación como el de "Narrame el
briefing" (shadow 2026-07-12). Gasta tokens contra el presupuesto del email
que pases; las respuestas y fallas quedan en ia.trazas → OBSERVABILIDAD.

Uso (Droplet):
    python -m scripts.bateria_home --email mollonicolas95@gmail.com
    python -m scripts.bateria_home --email ... --desde 8   # retomar desde la N
"""
from __future__ import annotations

import argparse
import time

from api.services import copiloto

PREGUNTAS = [
    # la narración que falló en el shadow (verificación) — la primera a vigilar
    copiloto._PREGUNTA_NARRAR_BRIEFING,
    "¿Cómo viene el mercado hoy?",
    # renta fija segmentada (el feedback central del shadow)
    "¿La renta fija está subiendo por la parte corta o la larga?",
    "¿Qué anda mejor hoy, los CER o los globales?",
    "¿Cómo vino cada curva en el mes?",
    # dólares y tasas
    "¿El canje se está abriendo o cerrando? ¿Qué leés de eso?",
    "¿Cómo vienen MEP, CCL y el oficial en el mes?",
    "¿Cuánta devaluación tiene puesta en precios la curva de futuros de dólar?",
    "¿Cuánto pagan las cauciones hoy?",
    # acciones y commodities, separados
    "¿Cómo están las acciones hoy? ¿Qué rubros empujan?",
    "¿Cómo vienen los granos y la energía hoy?",
    "¿Cómo viene el MERVAL en el año?",
    # honestidad (describe, no inventa causas) y derivación
    "¿Por qué está subiendo el dólar?",
    "¿Qué CEDEAR me conviene comprar hoy?",  # → debería derivar a Renta Variable
    "¿Qué pregunta importante debería hacerte que no te hice?",  # meta
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="cuenta contra el presupuesto de este email")
    ap.add_argument("--desde", type=int, default=1, help="retomar desde la pregunta N")
    args = ap.parse_args()

    ok = fallas = 0
    for i, pregunta in enumerate(PREGUNTAS, start=1):
        if i < args.desde:
            continue
        print(f"\n{'=' * 74}\n[{i}/{len(PREGUNTAS)}] {pregunta}\n{'-' * 74}")
        out = copiloto.preguntar("home", pregunta, usuario=args.email)
        if out.get("ok"):
            ok += 1
            print(out["respuesta"])
            if out.get("vista_sugerida"):
                print(f"→ deriva a: {out['vista_sugerida']}")
        else:
            fallas += 1
            print(f"✗ {out.get('error')}")
        time.sleep(2)

    print(f"\n{'=' * 74}\n{ok} respondidas · {fallas} con falla "
          "(las fallas quedaron en ia.trazas con su motivo)")


if __name__ == "__main__":
    main()
