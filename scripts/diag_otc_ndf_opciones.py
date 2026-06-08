"""scripts/diag_otc_ndf_opciones.py — ¿los tipo_operacion OTC (NDF / Opciones OTC)
se toman para algo? ¿cuánto pesan? (read-only, REGLA #2).

Antes de borrar definitivamente los boletos "Rueda OTC NDF OTC - Compra/Venta" y
"Rueda OTC Opciones OTC - Compra/Venta" de CashFlow.Operaciones, este diag mide:

  1) FAMILIA OTC completa: todos los `tipo_operacion` que contienen "OTC" con su
     conteo — para no borrar de menos ni de más (quizá hay más variantes).
  2) TARGET (NDF OTC + Opciones OTC): por (operacion, mercado, moneda) → count +
     Σ|bruto| + desglose es_cierre / etapa. Esto muestra EXACTAMENTE cómo entran
     a la vista OPERACIONES (que agrega por `operacion`/`moneda`/`bruto`).
  3) VOLUMEN que aportan HOY a OPERACIONES: Σ|bruto| de los que SÍ suman
     (es_cierre=False y etapa≠solicitud) — es la plata que DESAPARECERÍA de los
     totales si se borran. Por moneda.
  4) ESPACIO: docs × avgObjSize (collStats) → MB aproximados que se liberan.
  5) RANGO temporal (primera/última concertacion).

NO borra nada. Con estos números se decide el borrado + el filtro de ingesta.

Uso (en el Droplet):
    python -m scripts.diag_otc_ndf_opciones
"""
from __future__ import annotations

import re

from core.mongo import get_mongo_client_read

# Los 4 tipo_operacion que el user quiere borrar. Se hace match por regex
# case-insensitive sobre `tipo_operacion` para tolerar espacios/variantes.
TARGET_REGEX = re.compile(r"(NDF\s*OTC|Opciones\s*OTC)", re.IGNORECASE)


def _fmt(n) -> str:
    return f"{n:,.2f}" if isinstance(n, (int, float)) else str(n)


def main() -> int:
    mdb = get_mongo_client_read()
    coll = mdb["CashFlow"]["Operaciones"]

    # ── 1) Familia OTC: todos los tipo_operacion con "OTC" ──────────────────
    print("=" * 78)
    print("1) FAMILIA OTC — todos los tipo_operacion que contienen 'OTC'")
    print("=" * 78)
    familia = list(coll.aggregate([
        {"$match": {"tipo_operacion": {"$regex": "OTC", "$options": "i"}}},
        {"$group": {"_id": "$tipo_operacion", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]))
    if not familia:
        print("  (no hay ningún doc con 'OTC' en tipo_operacion)")
    total_otc = 0
    for r in familia:
        es_target = "  ← TARGET" if TARGET_REGEX.search(r["_id"] or "") else ""
        total_otc += r["n"]
        print(f"  {r['n']:>8,}  {r['_id']}{es_target}")
    print(f"  {'─' * 40}\n  TOTAL familia OTC: {total_otc:,} docs")

    # ── 2) TARGET: cómo entran a la vista OPERACIONES ───────────────────────
    print("\n" + "=" * 78)
    print("2) TARGET (NDF OTC + Opciones OTC) — por operacion/mercado/moneda")
    print("=" * 78)
    match_target = {"tipo_operacion": {"$regex": "NDF\\s*OTC|Opciones\\s*OTC", "$options": "i"}}
    n_target = coll.count_documents(match_target)
    print(f"  Docs target totales: {n_target:,}")
    if n_target == 0:
        print("\n  ⚠ Cero docs target — no hay nada que borrar. Fin.")
        return 0

    desglose = list(coll.aggregate([
        {"$match": match_target},
        {"$group": {
            "_id": {
                "operacion": "$operacion", "mercado": "$mercado", "moneda": "$moneda",
                "es_cierre": "$es_cierre", "etapa": "$etapa",
            },
            "n": {"$sum": 1},
            "bruto_abs": {"$sum": {"$abs": {"$ifNull": ["$bruto", 0]}}},
        }},
        {"$sort": {"bruto_abs": -1}},
    ]))
    print(f"  {'operacion':<22}{'mercado':<12}{'mon':<5}{'cierre':<7}{'etapa':<11}{'n':>7}{'Σ|bruto|':>20}")
    print("  " + "-" * 84)
    for r in desglose:
        k = r["_id"]
        print(f"  {k.get('operacion')!s:<22}{k.get('mercado')!s:<12}"
              f"{k.get('moneda')!s:<5}{k.get('es_cierre')!s:<7}"
              f"{k.get('etapa')!s:<11}{r['n']:>7,}{_fmt(r['bruto_abs']):>20}")

    # ── 3) VOLUMEN que aportan HOY a OPERACIONES (lo que se perdería) ───────
    print("\n" + "=" * 78)
    print("3) VOLUMEN que SE PERDERÍA de la vista OPERACIONES si se borran")
    print("   (filtro real de la vista: es_cierre=False y etapa≠solicitud)")
    print("=" * 78)
    vol = list(coll.aggregate([
        {"$match": {**match_target, "es_cierre": False, "etapa": {"$ne": "solicitud"}}},
        {"$group": {"_id": "$moneda", "n": {"$sum": 1},
                    "bruto_abs": {"$sum": {"$abs": {"$ifNull": ["$bruto", 0]}}}}},
        {"$sort": {"bruto_abs": -1}},
    ]))
    if not vol:
        print("  ✓ Ninguno suma a la vista (todos son cierre/solicitud) → borrarlos NO cambia los totales.")
    for r in vol:
        print(f"  moneda={r['_id']!s:<6}  n={r['n']:>7,}  Σ|bruto|={_fmt(r['bruto_abs']):>20}")

    # ── 4) ESPACIO aproximado ───────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("4) ESPACIO aproximado que se libera")
    print("=" * 78)
    try:
        stats = mdb["CashFlow"].command("collStats", "Operaciones")
        avg = stats.get("avgObjSize", 0)
        total_docs = stats.get("count", 0)
        mb = (avg * n_target) / (1024 * 1024)
        print(f"  avgObjSize={avg} bytes · docs target={n_target:,}")
        print(f"  ≈ {mb:.2f} MB de {stats.get('size', 0)/(1024*1024):.1f} MB totales "
              f"({n_target/total_docs*100:.2f}% de los {total_docs:,} docs)")
    except Exception as e:
        print(f"  (no se pudo leer collStats: {e})")

    # ── 5) RANGO temporal ───────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("5) RANGO temporal (concertacion)")
    print("=" * 78)
    rango = next(iter(coll.aggregate([
        {"$match": match_target},
        {"$group": {"_id": None, "min": {"$min": "$concertacion"}, "max": {"$max": "$concertacion"}}},
    ])), None)
    if rango:
        print(f"  primera={rango.get('min')}   última={rango.get('max')}")

    print("\nLISTO (read-only, nada se modificó).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
