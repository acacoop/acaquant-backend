"""audit_assets_instrumento.py — read-only.

Audita el campo `INSTRUMENTO` de Valuaciones.Assets cruzando contra
Manager.PyRofexInstruments (lista canónica primary BYMA 24hs).

Reporta:
  - Cuántos Assets tienen INSTRUMENTO seteado (no-vacío, no-NO APLICA).
  - Cuántos de esos coinciden con un symbol real en pyRofex 24hs ✓.
  - Cuántos no coinciden ⚠ — probables strings ciegos sin feed.

NO modifica nada. Solo reporta.

Uso:
    python -m scripts.audit_assets_instrumento
    python -m scripts.audit_assets_instrumento --top 30
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

_PLACEHOLDERS = {"", "NO APLICA"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top", type=int, default=20,
                        help="Cantidad de sospechosos a listar (default 20).")
    args = parser.parse_args()

    client = get_mongo_client()

    # 1) Set canónico — solo primary BYMA 24hs.
    pyrofex_24hs: set[str] = set()
    pyrofex_otros_plazos: dict[str, set[str]] = {}  # ticker_corto → {plazos}
    for doc in client["Manager"]["PyRofexInstruments"].find(
        {}, {"_id": 0, "instruments.ticker": 1}
    ):
        for inst in doc.get("instruments") or []:
            t = (inst.get("ticker") or "").strip()
            if not t.startswith("MERV - XMEV - "):
                continue
            if t.endswith(" - 24hs"):
                pyrofex_24hs.add(t)
            else:
                # Capturamos los otros plazos para sugerir alternativas.
                # Formato: "MERV - XMEV - <ticker> - <plazo>"
                partes = t.split(" - ")
                if len(partes) == 4:
                    tk, plazo = partes[2], partes[3]
                    pyrofex_otros_plazos.setdefault(tk, set()).add(plazo)
    print(f"PyRofex primary 24hs:           {len(pyrofex_24hs)}")
    print(f"PyRofex primary otros plazos:   {sum(len(v) for v in pyrofex_otros_plazos.values())} entries\n")

    # 2) Assets con INSTRUMENTO seteado.
    coll = client["Valuaciones"]["Assets"]
    docs = list(coll.find(
        {"INSTRUMENTO": {"$nin": list(_PLACEHOLDERS) + [None]}},
        {"_id": 0, "unidad": 1, "INSTRUMENTO": 1, "CARTERA": 1},
    ))
    print(f"Assets con INSTRUMENTO seteado: {len(docs)}")

    validos: list = []
    sospechosos: list = []
    for d in docs:
        inst = (d.get("INSTRUMENTO") or "").strip()
        if inst in pyrofex_24hs:
            validos.append(d)
        else:
            sospechosos.append(d)
    print(f"  ✓ válidos en pyRofex 24hs:    {len(validos)}")
    print(f"  ⚠ NO en pyRofex 24hs:          {len(sospechosos)}")

    if not sospechosos:
        print("\nNada para revisar — todos los INSTRUMENTOs son válidos.")
        return 0

    # 3) Para cada sospechoso, ¿existe en otro plazo? Sugerimos.
    print(f"\n-- TOP {args.top} SOSPECHOSOS (puede que operen en otro plazo) --")
    for d in sospechosos[:args.top]:
        inst = d.get("INSTRUMENTO") or ""
        unidad = d.get("unidad") or ""
        # Extraer ticker corto de "MERV - XMEV - X - 24hs".
        partes = inst.split(" - ")
        ticker = partes[2] if len(partes) >= 3 else "?"
        plazos_disponibles = pyrofex_otros_plazos.get(ticker, set())
        sugerencia = f" (existe en: {', '.join(sorted(plazos_disponibles))})" if plazos_disponibles else ""
        print(f"  {unidad}")
        print(f"    INSTRUMENTO seteado: {inst}{sugerencia}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
