"""
build_contrapartes_resumen.py — Agrega Bruto por contraparte y moneda (2026).

Flujo:
  1. Lee CashFlow.Operaciones filtrando Concertación >= 2026-01-01
  2. Join con CashFlow.Contrapartes por Denominación → contraparte
  3. Detecta moneda desde Condiciones: contiene "USD" → USD, contiene "ARS" → ARS
  4. Agrupa por (contraparte, moneda) y suma Bruto
  5. Upsert en CashFlow.ContrapartesResumen

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/Excel/build_contrapartes_resumen.py
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mongo_manager import get_mongo_client


def detectar_moneda(condiciones):
    s = str(condiciones).upper()
    if "USD" in s:
        return "USD"
    if "ARS" in s:
        return "ARS"
    return "OTRO"


if __name__ == "__main__":
    client = get_mongo_client()
    db = client["CashFlow"]

    # ── Join via aggregation pipeline ────────────────────────────────────────
    pipeline = [
        # Solo operaciones de 2026
        {"$match": {"Concertación": {"$gte": "2026-01-01"}}},

        # Join con Contrapartes por Denominación
        {"$lookup": {
            "from":         "Contrapartes",
            "localField":   "Denominación",
            "foreignField": "denominacion",
            "as":           "_cp",
        }},

        # Descartar los que no tienen contraparte registrada
        {"$match": {"_cp": {"$ne": []}}},

        {"$unwind": "$_cp"},

        # Proyectar los campos que necesitamos
        {"$project": {
            "_id":          0,
            "contraparte":  "$_cp.contraparte",
            "condiciones":  "$Condiciones",
            "bruto":        {"$toDouble": {"$ifNull": ["$Bruto", 0]}},
        }},
    ]

    rows = list(db["Operaciones"].aggregate(pipeline))
    print(f"Registros 2026 con contraparte reconocida: {len(rows)}")

    # ── Agrupar en Python por (contraparte, moneda) ───────────────────────────
    totales = {}
    for r in rows:
        moneda = detectar_moneda(r["condiciones"])
        key    = (r["contraparte"], moneda)
        totales[key] = totales.get(key, 0.0) + r["bruto"]

    # ── Upsert en ContrapartesResumen ─────────────────────────────────────────
    col = db["ContrapartesResumen"]
    col.create_index([("contraparte", 1), ("moneda", 1)], unique=True, background=True)

    ts = datetime.utcnow()
    for (contraparte, moneda), bruto in sorted(totales.items()):
        col.update_one(
            {"contraparte": contraparte, "moneda": moneda},
            {"$set": {
                "contraparte": contraparte,
                "moneda":      moneda,
                "bruto":       round(bruto, 2),
                "updated_at":  ts,
            }},
            upsert=True,
        )
        print(f"  {contraparte:<22}  {moneda:<4}  {bruto:>18,.2f}")

    print(f"\n✅ ContrapartesResumen actualizado — {len(totales)} registros")
    client.close()
