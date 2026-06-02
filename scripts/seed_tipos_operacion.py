"""seed_tipos_operacion.py — catálogo CashFlow.TiposOperacion (tipo → mercado).

Es la tabla MERCADOS: cada `tipo_operacion` mapea a un `mercado` (que cargás vos:
BYMA, SENEBI, MAV, Primario, FCI, etc.) para poder filtrar las operaciones por
mercado. El `operacion` (sentido: compra/venta/caucion/...) se deriva solo.

Fuentes de tipos:
  1. DISTINCT `tipo_operacion` de CashFlow.Operaciones (lo que realmente hay).
  2. La lista base conocida (por si Operaciones todavía no cargó del todo).

Idempotente: upsert por tipo_operacion. Refresca `operacion` (derivado) pero
NUNCA pisa el `mercado` que ya cargaste ($setOnInsert).

Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.seed_tipos_operacion
    venv/bin/python -m scripts.seed_tipos_operacion --dry
"""
from __future__ import annotations

import argparse
import unicodedata

from pymongo import UpdateOne

from core.mongo import get_mongo_client

# Lista base conocida (se une con los DISTINCT reales de Operaciones).
_TIPOS_BASE = [
    "COLP - Emisión", "COLP - Licitación",
    "Concurrencia - Caución colocadora (Cierre)",
    "Concurrencia - Caución tomadora (Cierre)",
    "Concurrencia Contado - Compra", "Concurrencia Contado - Venta",
    "ECHEQ - Compra", "ECHEQ - Subasta",
    "Licitación", "Licitación - Compra", "Licitación - Venta",
    "Liquidación de rescate de FCI ACDI", "Liquidación de suscripción de FCI ACDI",
    "Opciones - Compra", "Opciones - Venta",
    "Pagarés - Compra", "Pagarés - Subasta",
    "Rescate final", "Rescate provisional",
    "SENEBI Contado - Compra", "SENEBI Contado - Venta",
    "Suscripción final", "Suscripción provisional",
    "TIVA (Compra)", "TIVA (Venta)",
    "TRD  (Compra)", "TRD  (Venta)", "TRD Vta/Cmp (Compra)",
]


def _sin_acentos(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def derivar_operacion(tipo: str) -> str:
    """Sentido de la operación, derivado del string del tipo."""
    t = _sin_acentos(tipo)
    if "caucion colocadora" in t:
        return "caucion_colocadora"
    if "caucion tomadora" in t:
        return "caucion_tomadora"
    if "rescate" in t:
        return "rescate"
    if "suscrip" in t:
        return "suscripcion"
    if "emision" in t:
        return "emision"
    if "(compra)" in t or "- compra" in t:
        return "compra"
    if "(venta)" in t or "- venta" in t:
        return "venta"
    if "subasta" in t:
        return "subasta"
    if "licitacion" in t:
        return "licitacion"
    if "compra" in t:
        return "compra"
    if "venta" in t:
        return "venta"
    return "otro"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="no escribe, solo muestra el catálogo")
    args = ap.parse_args()

    client = get_mongo_client()
    ops = client["CashFlow"]["Operaciones"]
    cat = client["CashFlow"]["TiposOperacion"]

    # DISTINCT reales + count por tipo (para priorizar al cargar mercados).
    reales = {}
    try:
        for r in ops.aggregate([
            {"$match": {"tipo_operacion": {"$ne": None}}},
            {"$group": {"_id": "$tipo_operacion", "n": {"$sum": 1}}},
        ]):
            reales[r["_id"]] = r["n"]
    except Exception as e:
        print(f"(no pude leer Operaciones: {e})")

    tipos = sorted(set(_TIPOS_BASE) | set(reales))
    print(f"{len(tipos)} tipos ({len(reales)} reales en Operaciones + base)\n")
    print(f"{'TIPO_OPERACION':<48} {'OPERACION':<20} {'#OPS':>9}")
    print("-" * 80)
    for t in tipos:
        print(f"{t[:47]:<48} {derivar_operacion(t):<20} {reales.get(t, 0):>9}")

    if args.dry:
        print("\n[DRY] No se escribió nada.")
        return

    cat.create_index([("tipo_operacion", 1)], name="uq_tipo", unique=True)
    ops_bulk = [
        UpdateOne(
            {"tipo_operacion": t},
            {
                "$set": {"operacion": derivar_operacion(t)},       # refresca el sentido
                "$setOnInsert": {"tipo_operacion": t, "mercado": ""},  # no pisa tu mercado
            },
            upsert=True,
        )
        for t in tipos
    ]
    res = cat.bulk_write(ops_bulk, ordered=False)
    print(f"\n✅ Catálogo CashFlow.TiposOperacion: {res.upserted_count} nuevos, "
          f"{res.modified_count} actualizados, {len(tipos)} total.")
    print("Cargá el `mercado` de cada uno (Mongo o la vista que armemos).")


if __name__ == "__main__":
    main()
