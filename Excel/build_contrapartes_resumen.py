"""
build_contrapartes_resumen.py — Migra movimientos de contrapartes conocidas.

Flujo:
  1. Lee TODOS los registros de CashFlow.Operaciones
  2. Join con CashFlow.Contrapartes por Denominación → contraparte
  3. Solo procesa los que tienen contraparte reconocida
  4. Guarda en CashFlow.ContrapartesResumen con campos limpios:
       boleto, concertacion, denominacion, contraparte, tipo_operacion,
       instrumento, condiciones, moneda, bruto
  5. Borra esos registros de CashFlow.Operaciones

Idempotente: upsert por boleto, no duplica si se corre más de una vez.

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/Excel/build_contrapartes_resumen.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mongo_manager import get_mongo_client


def detectar_moneda(condiciones):
    s = str(condiciones).upper()
    if "USD" in s:
        return "USD"
    if "ARS" in s:
        return "ARS"
    return "OTRO"


def parse_numero(v):
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return 0.0


if __name__ == "__main__":
    client = get_mongo_client()
    db = client["CashFlow"]

    col_ops = db["Operaciones"]
    col_res = db["ContrapartesResumen"]
    col_cp  = db["Contrapartes"]

    # Limpiar colección si tiene datos del formato viejo (sin campo boleto)
    if col_res.count_documents({"boleto": {"$exists": False}}) > 0:
        print("⚠️  Datos viejos detectados — limpiando ContrapartesResumen...")
        col_res.drop()

    # Índice único por boleto
    col_res.create_index("boleto", unique=True, background=True)

    # Construir mapa denominacion → contraparte en memoria
    cp_map = {d["denominacion"]: d["contraparte"] for d in col_cp.find({}, {"_id": 0})}
    print(f"Contrapartes registradas: {len(cp_map)}")

    total_ops = col_ops.count_documents({})
    print(f"Registros en Operaciones: {total_ops}")

    BATCH = 500
    migrados_total = 0
    skip = 0

    while True:
        batch = list(col_ops.find({}).skip(skip).limit(BATCH))
        if not batch:
            break

        a_upsert = []
        a_borrar = []

        for r in batch:
            den = r.get("Denominación", "")
            contraparte = cp_map.get(den)
            if not contraparte:
                continue

            boleto = r.get("Boleto", "")
            a_upsert.append({
                "boleto":         boleto,
                "concertacion":   r.get("Concertación", ""),
                "denominacion":   den,
                "contraparte":    contraparte,
                "tipo_operacion": r.get("Tipo de operación", ""),
                "instrumento":    r.get("Instrumento", ""),
                "condiciones":    r.get("Condiciones", ""),
                "moneda":         detectar_moneda(r.get("Condiciones", "")),
                "bruto":          parse_numero(r.get("Bruto", 0)),
            })
            a_borrar.append(r["_id"])

        if a_upsert:
            from pymongo import UpdateOne
            ops = [
                UpdateOne({"boleto": d["boleto"]}, {"$set": d}, upsert=True)
                for d in a_upsert
            ]
            col_res.bulk_write(ops, ordered=False)
            col_ops.delete_many({"_id": {"$in": a_borrar}})
            migrados_total += len(a_upsert)

        skip += BATCH
        print(f"  procesados {skip}/{total_ops} — migrados acumulados: {migrados_total}", flush=True)

    print(f"\n✅ Movimientos migrados a ContrapartesResumen: {migrados_total}")

    client.close()
