"""scripts/diag_dups_check.py — GARANTÍA de no-duplicados (regla dura del usuario).

JAMÁS puede haber duplicados en CashFlow.Operaciones ni en CashFlow.NegocioMov.
Esta garantía es ESTRUCTURAL: un índice ÚNICO la hace imposible a nivel Mongo
(un insert duplicado tira E11000 y rebota). Este diag read-only confirma DOS cosas:

  1. El índice único existe y está enforced:
       - Operaciones        → único por `boleto`
       - NegocioMovimientos → único por (`fecha`, `comprobante`)
  2. NO hay duplicados ahora mismo (por si el índice se creó después de cargar
     datos sucios, o no es realmente unique).

Si falta el índice único o aparecen duplicados → hay que limpiar y crear el índice
(un índice unique NO se puede crear si ya hay duplicados → primero se limpian).

Read-only. Correr en el Droplet:
    python -m scripts.diag_dups_check
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def _indice_unico(coll, claves: list[tuple[str, int]]) -> str | None:
    """Nombre del índice ÚNICO que matchea exactamente `claves`, o None."""
    for nombre, info in coll.index_information().items():
        if info.get("unique") and list(info.get("key", [])) == claves:
            return nombre
    return None


def _dups(coll, campos: list[str]) -> list[dict]:
    """Grupos con count>1 por `campos` (los duplicados reales). Top 10."""
    gid = {c: f"${c}" for c in campos}
    return list(coll.aggregate([
        {"$group": {"_id": gid, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 10},
    ], allowDiskUse=True))


def _check(coll, etiqueta: str, claves: list[tuple[str, int]]) -> bool:
    campos = [k for k, _ in claves]
    ix = _indice_unico(coll, claves)
    dups = _dups(coll, campos)
    print(f"── {etiqueta} (único por {', '.join(campos)}) ──")
    print(f"  índice ÚNICO enforced : {'✓ ' + ix if ix else '❌ FALTA (no es unique)'}")
    if dups:
        print(f"  duplicados AHORA      : ❌ {len(dups)}+ grupos. Ejemplos:")
        for d in dups[:5]:
            print(f"      {d['_id']}  → {d['n']} copias")
    else:
        print("  duplicados AHORA      : ✓ ninguno")
    return bool(ix) and not dups


def main() -> int:
    db = get_mongo_client_read()["CashFlow"]
    ok_ops = _check(db["Operaciones"], "Operaciones", [("boleto", 1)])
    print()
    ok_mov = _check(db["NegocioMovimientos"], "NegocioMovimientos",
                    [("fecha", 1), ("comprobante", 1)])
    print()
    if ok_ops and ok_mov:
        print("✅ Regla 'jamás duplicados' GARANTIZADA en ambas colecciones.")
        return 0
    print("⚠ Falta enforcement o hay duplicados — limpiar y crear el índice único.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
