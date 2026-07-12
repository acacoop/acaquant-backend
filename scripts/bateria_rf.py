"""bateria_rf.py — batería de 15 preguntas al copiloto de RENTA FIJA.

Corre las 15 contra el copiloto REAL (datos vivos + DeepSeek) para mapear qué
responde bien, qué le falta y qué conviene precalcular. Gasta ~150k tokens
(≈15 × 10k) contra el presupuesto del email que pases (tope por usuario 1M/día,
el contador se resetea a medianoche UTC). Las respuestas quedan también en
ia.trazas → panel OBSERVABILIDAD (pedido/respuesta por llamada).

Uso (Droplet):
    python -m scripts.bateria_rf --email mollonicolas95@gmail.com
    python -m scripts.bateria_rf --email ... --desde 8   # retomar desde la N
"""
from __future__ import annotations

import argparse
import time

from api.services import copiloto

PREGUNTAS = [
    # curvas y movimientos
    "¿Cómo están las curvas hoy? ¿Dónde está el premio por estirarse de plazo?",
    "¿Qué comprimió y qué descomprimió contra el último cierre?",
    # fair value
    "¿Qué está barato de verdad contra la curva CER?",
    "¿En tasa fija hay algo desarbitrado o está todo en línea con la curva?",
    # el marco PM
    "¿Tasa fija o CER para los próximos 3 meses?",
    "¿Y para un año vista, cambia la respuesta?",
    "¿El mercado le cree al REM? ¿Dónde está la diferencia más grande?",
    # comparaciones y atribución
    "Compará TX26 contra TX28: ¿cuál me conviene y qué estoy asumiendo en cada caso?",
    "¿Por qué se movió como se movió TX26 en el último mes?",
    # soberanos
    "¿Cuánto paga GD30 si la TIR comprime un punto? ¿Y si sube uno?",
    "¿AL30 o GD30? ¿Qué me dice el spread entre ellos?",
    # dólar y cobertura
    "¿Qué TC breakeven tienen las lecaps cortas? ¿A cuánto me tiene que ir el MEP "
    "para que pierda contra el dólar?",
    "¿Los dollar-linked están pagando algo interesante?",
    # calidad de dato / honestidad
    "¿Hay algún CER corto ilíquido que pueda estar mostrando tasa vieja?",
    "¿A cuánto está el riesgo país hoy?",  # fuera de alcance → debe decir que no lo tiene
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
        out = copiloto.preguntar("renta_fija", pregunta, usuario=args.email)
        if out.get("ok"):
            ok += 1
            print(out["respuesta"])
        else:
            fallas += 1
            print(f"✗ {out.get('error')}")
        time.sleep(2)

    print(f"\n{'=' * 74}\n{ok} respondidas · {fallas} con falla "
          "(las fallas quedaron en ia.trazas con su motivo)")


if __name__ == "__main__":
    main()
