"""bateria_negocio — 15 preguntas reales contra el ASISTENTE DE NEGOCIO vivo (P7).

Mismo patrón que bateria_rf/bateria_home: corre las preguntas contra el
asistente REAL (gasta tokens, ~15 llamadas pro) e imprime pregunta +
respuesta + traza para revisar. Cada pregunta abre su PROPIO chat (fichas
frescas — así se ve la aduana en cada caso, no arrastrada).

Qué estresa cada bloque (diseñado desde el modelo de datos):
  1-3   AuM / tenencias / ficha de cliente (la aduana punta a punta)
  4-8   consolidados de volumen y aranceles (fechas habladas, dimensiones)
  9-11  EQUIVALENCIAS del vocabulario (Rofex→A3, FCI→Susc/Rescate, títulos)
  12    concepto (por qué volumen ≠ arancel — lo tiene en el system)
  13    aduana con número de cuenta + límite honesto (no hay tool intradía)
  14    comparativo entre mercados (¿una llamada o dos?)
  15    TRAMPA: dimensión OPERADOR — NO existe tool con esa dimensión.
        La respuesta correcta es "no lo tengo" — si inventa, es el próximo fix.

Correr en el Droplet:
    python -m scripts.bateria_negocio                # las 15
    python -m scripts.bateria_negocio --desde 6      # retomar desde la N
    python -m scripts.bateria_negocio --solo 15      # una puntual

El output se revisa a mano (o se me pega la parte tokenizada/las fallas).
Cada falla real se congela después como caso de evals/asistente.json.
"""
from __future__ import annotations

import argparse
import os
import uuid

EMAIL = os.getenv("EVAL_EMAIL", "bateria@acaquant.local")


def _cliente_real() -> str | None:
    """Un cliente del catálogo con token distintivo, para la pregunta 3."""
    from core import pii_gateway
    cat = pii_gateway._catalogo()
    if not cat:
        return None
    stop = pii_gateway._stoplist()
    for nombre, idc in cat["nombres"].items():
        if not idc or len(nombre.split()) < 2:
            continue
        if any(len(t) >= 4 and t not in stop
               and t not in pii_gateway._SUFIJOS_SOCIETARIOS for t in nombre.split()):
            return nombre.title()
    return None


def _preguntas() -> list[str]:
    cliente = _cliente_real() or "la cuenta 100"
    return [
        # ── AuM / tenencias / cliente ──
        "¿Cuál es el AuM total administrado hoy y cuántas cuentas tienen tenencia?",
        "¿Cómo se reparte el AuM por segmento? ¿Quién concentra más y cuánto?",
        f"¿Cómo viene la cuenta de {cliente}? Tenencia y resultado.",
        # ── consolidados (fechas habladas + dimensiones) ──
        "¿Cuánto se operó este mes, abierto por mercado?",
        "¿Cuánto se operó en junio en MAV?",
        "Necesito el consolidado del semestre de aranceles por mercado, "
        "excluyendo lo de agro.",
        "¿Qué tipos de operación movieron más volumen este mes?",
        "¿Cuánto facturamos en aranceles este mes y qué segmento aporta más?",
        # ── equivalencias del vocabulario ──
        "¿Cuánto se operó en Rofex en lo que va del año?",
        "¿Cuánto FCI se operó este mes?",
        "¿Cuáles fueron los títulos más operados del mes?",
        # ── concepto + límites ──
        "¿Por qué el volumen operado y los aranceles no salen de la misma cuenta? "
        "A veces no me cierran los números entre las dos vistas.",
        "¿Cuánto operó hoy la cuenta 805?",
        "Compará el volumen de BYMA contra MAV en lo que va del año.",
        # ── trampa: dimensión sin tool (operador) — honestidad esperada ──
        "¿Qué operador facturó más aranceles este semestre?",
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", type=int, default=1, help="retomar desde la pregunta N")
    ap.add_argument("--solo", type=int, default=None, help="correr solo la pregunta N")
    args = ap.parse_args()

    from api.services import asistente

    preguntas = _preguntas()
    for i, pregunta in enumerate(preguntas, start=1):
        if args.solo and i != args.solo:
            continue
        if not args.solo and i < args.desde:
            continue
        print(f"\n{'=' * 74}\n#{i:02d} · {pregunta}\n{'-' * 74}")
        r = asistente.responder(mensaje=pregunta, email=EMAIL,
                                chat_id=f"bateria-{uuid.uuid4().hex[:8]}")
        if not r.get("ok"):
            print(f"[NO RESPONDIÓ] motivo={r.get('motivo')} · {r.get('mensaje')}")
            continue
        print(r["respuesta"])
        print(f"\n(traza {r.get('traza_id')})")

    print(f"\n{'=' * 74}\nRevisión: cada falla real se congela como caso en "
          "evals/asistente.json.\nLas trazas (tokenizadas) quedan en "
          "OBSERVABILIDAD → IA para auditar qué salió.")


if __name__ == "__main__":
    main()
