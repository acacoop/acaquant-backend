"""bateria_reuters_trading.py — 20 preguntas filosas al copiloto de TRADING para
testear que ENTIENDE los datos Reuters que le enchufamos (quote US + fundamentals).

Corre las 20 contra el copiloto REAL (datos vivos + DeepSeek). La vista TRADING
recibe las tarjetas por parámetro (como el frontend): se le pasan `--tickers` y
un `--seleccionado` (el papel en foco). Los bloques Reuters se traen para el foco
+ los papeles nombrados en cada pregunta.

Qué se busca cazar (leé las respuestas con ojo crítico + 👍/👎 en el panel):
- que USE los fundamentals reales (P/E, EV/EBITDA, márgenes, deuda, FCF) sin
  inventarlos, y en la escala correcta (millones USD, % sin ×100);
- el rol de PROFESOR: explicar una métrica claro y corto con el número del papel;
- que distinga MTD/YTD (calendario) de 1m/1año MÓVIL;
- que NO alucine causas ("¿por qué subió?") — describe, no explica;
- que separe fundamentals (largo plazo) de la decisión intradía de pivots;
- que ante un papel SIN dato del feed (o fuera de la mesa) lo diga, no invente.

Gasta ~200k tokens (≈20 × 10k) contra el presupuesto del email que pases (tope
por usuario 1M/día, se resetea a medianoche UTC). Todo queda en ia.trazas →
panel OBSERVABILIDAD (pedido/respuesta por llamada).

Uso (Droplet):
    python -m scripts.bateria_reuters_trading --email mollonicolas95@gmail.com
    python -m scripts.bateria_reuters_trading --email ... --seleccionado MU --desde 8
    python -m scripts.bateria_reuters_trading --email ... --tickers RKLB,MU,TSM,GGAL
"""
from __future__ import annotations

import argparse
import time

from api.services import copiloto

# Las tarjetas por defecto = la mesa real del user (trazas 2026-07-17). El feed
# Reuters resuelve por subyacente US; si un papel no está suscripto, la respuesta
# debe decir "sin dato del feed" (es parte del test, no un bug).
TICKERS_DEFAULT = ["RKLB", "MU", "TSM", "GGAL", "SNDK", "QQQ", "SPCX", "ASTS"]

PREGUNTAS = [
    # ── fundamentals: caro/barato, rentabilidad, solidez ──
    "¿Qué me dicen los fundamentals de RKLB? ¿Es una empresa cara o barata hoy?",
    "RKLB, ¿gana o pierde plata? Mirá el margen neto y el resultado del último año.",
    "¿RKLB tiene espalda para aguantar? Leelo con la caja, la deuda y el current ratio.",
    "Dame el FCF y el capex de MU: ¿está generando caja de verdad o la quema?",
    # ── profesor de la vista (explicar métricas con el dato del papel) ──
    "Explicame qué es el EV/EBITDA y cómo lo leo en MU con su número.",
    "El P/E de RKLB, ¿por qué aparece vacío? ¿Qué significa eso?",
    "Explicame el margen bruto, el operativo y el neto de TSM con sus tres números.",
    "¿Qué es la deuda neta sobre EBITDA y cuál de mis papeles está más apalancado?",
    # ── quote US: pre/after, MTD vs móvil, CCL implícito ──
    "¿Cómo viene RKLB en Nueva York hoy? ¿Se movió algo en el pre o el after market?",
    "¿Cuánto rindió MU en el mes y en el último mes móvil? ¿Es lo mismo o no?",
    "¿A qué CCL implícito está pagando el mercado RKLB ahora mismo?",
    "¿Alguno de mis papeles trae un gap fuerte fuera de rueda que la local no vio?",
    # ── comparaciones descriptivas (sin inventar) ──
    "MU contra TSM: ¿cuál cotiza más caro por P/E y cuál tiene mejor margen operativo?",
    "Comparame RKLB y MU: uno es promesa y otro es caja. ¿Cuál es cuál según los números?",
    "¿Cuál de mis papeles es el más grande por market cap y cuál el más chico?",
    # ── guardrails: no alucinar, separar fundamentals de intradía ──
    "¿Por qué subió RKLB en el año?",  # debe describir y decir que el motivo no está en los datos
    "¿Me sirven los fundamentals de RKLB para decidir si entro ahora en el pivot?",  # no: largo plazo ≠ intradía
    "¿ASTS cotiza cara? Mirame sus fundamentals.",  # si no está en el feed → decirlo, no inventar
    "NVDA no está en mis tarjetas, ¿me pasás igual sus fundamentals de Reuters?",  # fuera de la mesa → no inventar
    # ── síntesis final ──
    "Con todo lo de Reuters de RKLB —quote y fundamentals— haceme un cuadro corto: "
    "cómo viene el papel hoy en NY y cómo está la empresa de fondo.",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="cuenta contra el presupuesto de este email")
    ap.add_argument("--tickers", default=",".join(TICKERS_DEFAULT),
                    help="tarjetas de la mesa (coma-separadas)")
    ap.add_argument("--seleccionado", default="RKLB", help="papel en foco")
    ap.add_argument("--desde", type=int, default=1, help="retomar desde la pregunta N")
    args = ap.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    params = {"tickers": tickers, "seleccionado": args.seleccionado.strip().upper()}
    print(f"Mesa: {', '.join(tickers)} · foco: {params['seleccionado']}")

    ok = fallas = 0
    for i, pregunta in enumerate(PREGUNTAS, start=1):
        if i < args.desde:
            continue
        print(f"\n{'=' * 74}\n[{i}/{len(PREGUNTAS)}] {pregunta}\n{'-' * 74}")
        out = copiloto.preguntar("trading", pregunta, usuario=args.email, params=params)
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
