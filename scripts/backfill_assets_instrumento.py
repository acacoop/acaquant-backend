"""backfill_assets_instrumento.py — auto-fill del campo `INSTRUMENTO`
en Valuaciones.Assets para tickers de mercado siguiendo el patrón
`MERV - XMEV - {ticker_corto} - 24hs`, validado contra
`Manager.PyRofexInstruments` (lista canónica de pyRofex).

Reglas:
  - Skip FCI (extract_cafci(unidad) != None o CARTERA contiene FCI):
    los FCI no se cotizan en mercado, su precio es VCP del fondo.
  - Skip unidades que empiezan con `[#` (carteras de financiamiento /
    instrumentos no estándar).
  - Skip si el ticker_corto extraído tiene caracteres no alfanuméricos.
  - Validación cruzada: el INSTRUMENTO calculado tiene que existir en
    `Manager.PyRofexInstruments`. Si no, NO se setea — se reporta como
    gap (probablemente opera en otro plazo / segmento).

ticker_corto se extrae con regex `^\\[\\d+\\]\\s*(.+?)(?=\\s+-\\s+|$)`.

Idempotente: si el doc ya tiene INSTRUMENTO no-vacío, no lo toca.

Pre-requisito: Manager.PyRofexInstruments debe estar fresca. Si está
desactualizada, correr antes:
    python -m scripts.discovery_pyrofex

Uso:
    python -m scripts.backfill_assets_instrumento              # ejecuta
    python -m scripts.backfill_assets_instrumento --dry        # solo reporta
    python -m scripts.backfill_assets_instrumento --top 30 --dry  # más muestra
"""
from __future__ import annotations

import argparse
import re

from core.cafci import extract_cafci
from core.mongo import get_mongo_client

_RE_TICKER_CORTO = re.compile(r"^\[\d+\]\s*(.+?)(?=\s+-\s+|$)")
_RE_TICKER_VALIDO = re.compile(r"^[A-Z0-9]+$")  # AL30, GD35, TZX26, etc.

_PLACEHOLDERS = {"", "NO APLICA"}


def _extract_ticker_corto(unidad: str) -> str | None:
    m = _RE_TICKER_CORTO.match(unidad or "")
    if not m:
        return None
    t = m.group(1).strip().upper()
    return t if _RE_TICKER_VALIDO.match(t) else None


def _es_skip(asset: dict) -> tuple[bool, str]:
    """Devuelve (skip, motivo). NO skipeamos por CARTERA vacía — varios
    activos reales (ONs USD como VSCHO, MSSEO, PNZCO, MR38O) tienen el
    ticker válido pero CARTERA sin cargar; perderíamos cobertura gratis.
    """
    unidad = asset.get("unidad") or ""
    cartera = (asset.get("CARTERA") or "").strip()
    if extract_cafci(unidad):
        return True, "fci_cafci"
    if "FCI" in cartera.upper():
        return True, "fci_cartera"
    if unidad.startswith("[#"):
        return True, "financiamiento"
    return False, ""


def _cargar_pyrofex_instrumentos(client) -> set[str]:
    """Set de symbols pyRofex con formato `MERV - XMEV - * - 24hs`.

    Levanta TODOS los instruments de Manager.PyRofexInstruments (un doc
    por CFI con instruments[]) y filtra los del primary BYMA en plazo
    24hs (la convención de mesa).
    """
    coll = client["Manager"]["PyRofexInstruments"]
    out: set[str] = set()
    for doc in coll.find({}, {"_id": 0, "instruments.ticker": 1}):
        for inst in doc.get("instruments") or []:
            t = (inst.get("ticker") or "").strip()
            if t.startswith("MERV - XMEV - ") and t.endswith(" - 24hs"):
                out.add(t)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry", action="store_true",
                        help="Solo reporta cuántos cambiarían y muestra ejemplos.")
    parser.add_argument("--top", type=int, default=15,
                        help="Cantidad de ejemplos a mostrar en --dry.")
    args = parser.parse_args()

    client = get_mongo_client()
    coll = client["Valuaciones"]["Assets"]

    # Set canónico de instruments para validación cruzada.
    pyrofex_set = _cargar_pyrofex_instrumentos(client)
    print(f"PyRofex instruments primary 24hs: {len(pyrofex_set)}")
    if not pyrofex_set:
        print("⚠ Manager.PyRofexInstruments vacía. "
              "Corré primero: python -m scripts.discovery_pyrofex")
        return 1

    # Match: docs sin INSTRUMENTO seteado.
    q = {
        "$or": [
            {"INSTRUMENTO": {"$in": list(_PLACEHOLDERS)}},
            {"INSTRUMENTO": {"$exists": False}},
            {"INSTRUMENTO": None},
        ],
    }
    docs = list(coll.find(q, {"_id": 1, "unidad": 1, "CARTERA": 1, "TICKER": 1}))
    print(f"Assets sin INSTRUMENTO: {len(docs)}\n")

    counters = {"set": 0, "fci_cafci": 0, "fci_cartera": 0,
                "financiamiento": 0, "ticker_invalido": 0,
                "no_en_pyrofex": 0}
    actualizar: list[tuple] = []
    ejemplos_set: list[tuple] = []
    ejemplos_skip: dict[str, list[str]] = {}

    for d in docs:
        skip, motivo = _es_skip(d)
        if skip:
            counters[motivo] += 1
            ejemplos_skip.setdefault(motivo, []).append(d.get("unidad") or "")
            continue
        ticker_corto = _extract_ticker_corto(d.get("unidad") or "")
        if not ticker_corto:
            counters["ticker_invalido"] += 1
            ejemplos_skip.setdefault("ticker_invalido", []).append(d.get("unidad") or "")
            continue
        instrumento = f"MERV - XMEV - {ticker_corto} - 24hs"
        # Validación cruzada contra la lista canónica de pyRofex.
        if instrumento not in pyrofex_set:
            counters["no_en_pyrofex"] += 1
            ejemplos_skip.setdefault("no_en_pyrofex", []).append(
                f"{d.get('unidad') or ''}  →  {instrumento}"
            )
            continue
        actualizar.append((d["_id"], instrumento, d.get("unidad") or ""))
        counters["set"] += 1
        if len(ejemplos_set) < args.top:
            ejemplos_set.append((d.get("unidad") or "", instrumento))

    print()
    print("=" * 70)
    print("PLAN DE BACKFILL")
    print("=" * 70)
    print(f"Para setear:                       {counters['set']:>5}")
    print(f"  Skip — FCI (CAFCI en unidad):    {counters['fci_cafci']:>5}")
    print(f"  Skip — FCI (CARTERA contiene FCI): {counters['fci_cartera']:>5}")
    print(f"  Skip — Financiamiento ([#...):   {counters['financiamiento']:>5}")
    print(f"  Skip — Ticker no alfanumérico:   {counters['ticker_invalido']:>5}")
    print(f"  Skip — NO en pyRofex 24hs:       {counters['no_en_pyrofex']:>5}  ← gap real para revisar")
    print()

    if args.dry:
        if ejemplos_set:
            print(f"-- TOP {args.top} A SETEAR --")
            for unidad, inst in ejemplos_set:
                print(f"  {unidad}")
                print(f"    → {inst}")
            print()
        for motivo, ejs in ejemplos_skip.items():
            if ejs:
                print(f"-- SKIP por {motivo} (top 5) --")
                for u in ejs[:5]:
                    print(f"  {u}")
                print()
        print("[DRY] no se modificó nada.")
        return 0

    if not actualizar:
        print("Nada para backfillear.")
        return 0

    n_updated = 0
    for _id, inst, _u in actualizar:
        coll.update_one({"_id": _id}, {"$set": {"INSTRUMENTO": inst}})
        n_updated += 1

    print(f"updated → {n_updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
