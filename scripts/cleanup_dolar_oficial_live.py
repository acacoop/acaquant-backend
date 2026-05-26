"""cleanup_dolar_oficial_live.py — deja en Valuaciones.DolarOficialLive SOLO el dólar oficial.

`DolarOficialLive` upsertea 1 doc por combo (ticker, codigoSegmento, codigoPlazo).
Con el tiempo MAE fue devolviendo combos que ya no manda (CNH$T, MB$T, UBEXP, ...) y
esos docs quedan huérfanos para siempre, ensuciando la colección. El sistema SOLO
lee `UST$T/M/000` (core.dolar_oficial.mid_oficial_live).

Este script borra TODO lo que no sea `UST$T/M/000`, dejando la colección con un
único doc: el dólar oficial mayorista. (El cliente mae_forex también se filtró
para mandar sólo ese, así no se vuelve a llenar.)

Uso (en el Droplet, desde la raíz):
    python -m scripts.cleanup_dolar_oficial_live              # DRY-RUN (no borra)
    python -m scripts.cleanup_dolar_oficial_live --apply      # borra de verdad
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

DB = "Valuaciones"
COL = "DolarOficialLive"

# El único instrumento que el sistema lee. Todo lo demás es ruido → se borra.
OFICIAL = {
    "data.ticker": "UST$T",
    "data.codigoSegmento": "M",
    "data.codigoPlazo": "000",
}


def _label(doc: dict) -> str:
    x = doc.get("data") or {}
    t = str(x.get("ticker"))
    s = str(x.get("codigoSegmento"))
    p = str(x.get("codigoPlazo"))
    return f"{t:>8} {s:>3} {p:>4}  upd={doc.get('updated_at')}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Deja sólo UST$T/M/000 en DolarOficialLive.")
    ap.add_argument("--apply", action="store_true",
                    help="borra de verdad. Sin esto, corre en DRY-RUN (no toca nada).")
    args = ap.parse_args()

    col = get_mongo_client()[DB][COL]
    docs = list(col.find({}, {"data.ticker": 1, "data.codigoSegmento": 1,
                              "data.codigoPlazo": 1, "updated_at": 1}))
    if not docs:
        print("Colección vacía, nada que hacer.")
        return 0

    # filtro de "NO es el oficial" para borrar
    filtro_borrar = {"$nor": [OFICIAL]}
    a_borrar = list(col.find(filtro_borrar, {"data.ticker": 1, "data.codigoSegmento": 1,
                                             "data.codigoPlazo": 1, "updated_at": 1}))
    a_mantener = [d for d in docs if d["_id"] not in {b["_id"] for b in a_borrar}]

    print(f"Total docs: {len(docs)}")
    print(f"\nMANTENER ({len(a_mantener)}) — el dólar oficial:")
    for d in a_mantener:
        print(f"   {_label(d)}")
    if not a_mantener:
        print("   ⚠️  NINGUNO. No existe el doc UST$T/M/000 todavía — corré el feed "
              "una vez antes de limpiar, o vas a dejar la colección vacía.")
    print(f"\nBORRAR ({len(a_borrar)}):")
    for d in a_borrar:
        print(f"   {_label(d)}")

    if not a_borrar:
        print("\nNada para borrar. La colección ya está limpia.")
        return 0

    if not args.apply:
        print(f"\n[DRY-RUN] No se borró nada. Re-corré con --apply para borrar los "
              f"{len(a_borrar)} docs de arriba.")
        return 0

    res = col.delete_many(filtro_borrar)
    print(f"\n✅ Borrados {res.deleted_count} docs. Quedan {len(a_mantener)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
