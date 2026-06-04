"""scripts/diag_fci_fuente.py — ¿El FCI de Operaciones es confiable, o debería
salir de NegocioMovimientos? MEDIR antes de decidir (REGLA #2).

Contexto: Operaciones se alimenta del API de informes, que NO trae FCI bilateral.
El job fci_bilateral.py lo INYECTA desde NegocioMovimientos (con etapa
solicitud/liquidacion). Ese job tuvo COLLSCAN y estuvo pausado → puede tener
huecos. Este diag read-only compara las dos fuentes para decidir A) arreglar la
inyección vs B) leer todo el FCI de NegocioMovimientos.

Tres bloques:
  A. NegocioMovimientos (fuente cruda): FCI por categoria × prefijo de comprobante
     (BOL = FCI normal que YA entra por API; CL = liquidación bilateral; DOC =
     solicitud del día). Cuánto y desde cuándo.
  B. Operaciones (lo que ve la vista): qué FCI hay — bilateral inyectado (por
     etapa) + FCI "normal", auto-descubierto por mercado/operacion (sin asumir).
  C. Reconciliación (últimos N días): de los comprobantes FCI que fci_bilateral
     DEBERÍA inyectar (CL + DOC), cuántos faltan en Operaciones (hueco real).

Read-only. Por default mide SOLO HOY (igualdad exacta de fecha). Correr en el Droplet:
    python -m scripts.diag_fci_fuente
    python -m scripts.diag_fci_fuente --fecha 2026-06-03
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read

_FCI_CATS = ("suscripcion_fci", "rescate_fci",
             "solicitud_suscripcion_fci", "solicitud_rescate_fci")


def _prefijo(comp: str | None) -> str:
    s = (comp or "").upper()
    for p in ("BOL", "CL", "DOC"):
        if s.startswith(p):
            return p
    return "(otro)"


def _bloque_a(mov, fecha: str) -> None:
    print("══ A. NegocioMovimientos (fuente cruda) — FCI por categoria × comprobante ══")
    rows = list(mov.aggregate([
        {"$match": {"categoria": {"$in": list(_FCI_CATS)}, "fecha": fecha}},
        {"$group": {
            "_id": {"cat": "$categoria", "comp": "$comprobante"},
            "n": {"$sum": 1}, "imp": {"$sum": {"$abs": {"$ifNull": ["$importe", 0]}}},
            "min": {"$min": "$fecha"}, "max": {"$max": "$fecha"},
        }},
    ], allowDiskUse=True))
    # Reagrupar por (categoria, prefijo) en Python.
    agg: dict[tuple[str, str], dict] = {}
    for r in rows:
        cat = r["_id"].get("cat") or "?"
        pref = _prefijo(r["_id"].get("comp"))
        a = agg.setdefault((cat, pref), {"n": 0, "imp": 0.0, "min": "9999", "max": "0"})
        a["n"] += r["n"]; a["imp"] += r["imp"]
        a["min"] = min(a["min"], r["min"] or "9999")
        a["max"] = max(a["max"], r["max"] or "0")
    print(f"  {'categoria':28} {'comp':6} {'n':>7} {'Σ|importe|':>18}  rango")
    for (cat, pref), a in sorted(agg.items()):
        print(f"  {cat:28} {pref:6} {a['n']:>7,} {a['imp']:>18,.0f}  {a['min']}…{a['max']}")


def _bloque_b(ops, fecha: str) -> None:
    print("\n══ B. Operaciones (lo que ve la vista) — FCI auto-descubierto ══")
    # Bilateral inyectado: mercado = "FCI Bilateral", por operacion × etapa.
    print("  ── inyectado (mercado='FCI Bilateral') por operacion × etapa ──")
    for r in ops.aggregate([
        {"$match": {"mercado": "FCI Bilateral", "concertacion": fecha}},
        {"$group": {"_id": {"op": "$operacion", "etapa": "$etapa"},
                    "n": {"$sum": 1}, "bruto": {"$sum": {"$ifNull": ["$bruto", 0]}}}},
        {"$sort": {"n": -1}},
    ]):
        op = r["_id"].get("op") or "?"; et = r["_id"].get("etapa") or "(sin etapa)"
        print(f"    {op:14} {et:14} n={r['n']:>6,}  Σbruto={r['bruto']:>16,.0f}")
    # FCI "normal": operacion Suscripción/Rescate FUERA del bilateral.
    print("  ── FCI normal (operacion Suscripción/Rescate, mercado ≠ FCI Bilateral) ──")
    rows = list(ops.aggregate([
        {"$match": {"operacion": {"$in": ["Suscripción", "Rescate"]},
                    "mercado": {"$ne": "FCI Bilateral"}, "concertacion": fecha}},
        {"$group": {"_id": {"op": "$operacion", "mercado": "$mercado"},
                    "n": {"$sum": 1}, "bruto": {"$sum": {"$ifNull": ["$bruto", 0]}}}},
        {"$sort": {"n": -1}},
    ]))
    if not rows:
        print("    (ninguno)")
    for r in rows:
        op = r["_id"].get("op") or "?"; mer = r["_id"].get("mercado") or "(sin mercado)"
        print(f"    {op:14} {mer:24} n={r['n']:>6,}  Σbruto={r['bruto']:>16,.0f}")


def _bloque_c(mov, ops, fecha: str) -> None:
    print(f"\n══ C. Reconciliación ({fecha}) — comprobantes que fci_bilateral DEBE inyectar ══")
    # Lo que el job inyecta: CL (liquidación) + DOC (solicitud). Las BOL ya entran
    # por el API → no las cuenta acá.
    q = {"fecha": fecha, "$or": [
        {"categoria": {"$in": ["suscripcion_fci", "rescate_fci"]},
         "comprobante": {"$regex": "^CL", "$options": "i"}},
        {"categoria": {"$in": ["solicitud_suscripcion_fci", "solicitud_rescate_fci"]}},
    ]}
    esperados = {str(d["comprobante"]).strip(): d
                 for d in mov.find(q, {"_id": 0, "comprobante": 1, "fecha": 1, "importe": 1})
                 if d.get("comprobante")}
    if not esperados:
        print("  (sin comprobantes FCI bilateral en la ventana)")
        return
    presentes = {str(b) for b in ops.distinct("boleto", {"boleto": {"$in": list(esperados)}})}
    faltan = [c for c in esperados if c not in presentes]
    imp_faltan = sum(abs(esperados[c].get("importe") or 0) for c in faltan)
    print(f"  esperados en Operaciones : {len(esperados):>7,} comprobantes (CL+DOC)")
    print(f"  presentes                : {len(presentes):>7,}")
    print(f"  FALTAN (hueco inyección) : {len(faltan):>7,}  · Σ|importe| no inyectado = {imp_faltan:,.0f}")
    if faltan:
        ej = sorted(faltan)[:8]
        print(f"  ejemplos faltantes: {', '.join(ej)}")
        print("  → la inyección tiene huecos. Decidir: A) arreglar fci_bilateral / "
              "B) leer FCI de NegocioMovimientos.")
    else:
        print("  ✓ inyección completa en la ventana — el FCI de Operaciones está al día.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha", help="día exacto YYYY-MM-DD (default: HOY ART)")
    args = ap.parse_args()
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()
    fecha = args.fecha or hoy.isoformat()
    db = get_mongo_client_read()["CashFlow"]
    mov, ops = db["NegocioMovimientos"], db["Operaciones"]
    print(f"Día (exacto): {fecha}\n")
    _bloque_a(mov, fecha)
    _bloque_b(ops, fecha)
    _bloque_c(mov, ops, fecha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
