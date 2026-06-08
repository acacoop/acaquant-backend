"""scripts/diag_fci_bruto_cero.py — ¿dónde se corta la corrección de bruto=0 de
las operaciones FCI? (read-only, REGLA #2).

Las suscripciones/rescates FCI entran a CashFlow.Operaciones con bruto=0 (el API de
informes no trae el monto; el real está en CashFlow.NegocioMovimientos). El parche
(jobs/fci_bilateral.py paso 6) las corrige en Mongo, pero las vistas LEEN de SQL.

Este diag mide, para los últimos días, en qué eslabón quedan en 0:
  - Mongo Operaciones: FCI con bruto=0/null → ¿cuántos? ¿tienen importe en NegocioMov
    (= son corregibles y el parche debería haberlos arreglado)?
  - SQL operaciones: FCI con bruto=0/null → ¿Mongo está bien pero SQL viene atrás?

Lectura:
  - Mongo con bruto=0 Y con importe en NegocioMov → el PARCHE no corrió/matcheó (Mongo).
  - Mongo OK pero SQL con bruto=0 → el SYNC no re-propagó el boleto corregido (SQL).

Uso (en el Droplet):
    python -m scripts.diag_fci_bruto_cero
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read
from core.postgres import connect

_DIAS = 15


def main() -> int:
    mdb = get_mongo_client_read()["CashFlow"]
    ops = mdb["Operaciones"]
    mov = mdb["NegocioMovimientos"]
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()
    desde = (hoy - timedelta(days=_DIAS)).isoformat()

    fci = {"tipo_operacion": {"$regex": "FCI", "$options": "i"}, "concertacion": {"$gte": desde}}
    fci_cero = {**fci, "bruto": {"$in": [0, None]}}

    total = ops.count_documents(fci)
    cero = ops.count_documents(fci_cero)
    print(f"== Mongo Operaciones (FCI, últimos {_DIAS}d) ==")
    print(f"  total FCI: {total}   con bruto=0/null: {cero}\n")

    rotos = list(ops.find(fci_cero, {"_id": 0, "boleto": 1, "concertacion": 1,
                                     "tipo_operacion": 1, "operacion": 1}).limit(60))
    con_importe = 0
    print(f"  Muestra de los bruto=0 ({len(rotos)} de {cero}) y si hay importe en NegocioMov:")
    for r in rotos:
        b = str(r.get("boleto") or "").strip()
        nm = mov.find_one({"comprobante": b, "importe": {"$nin": [None, 0]}},
                          {"_id": 0, "importe": 1})
        if nm:
            con_importe += 1
        flag = f"importe={nm['importe']}" if nm else "SIN importe en NegocioMov"
        print(f"    {b:14} {r.get('concertacion')}  {str(r.get('operacion')):12} {flag}")
    print(f"\n  → {con_importe}/{len(rotos)} muestreados TIENEN importe en NegocioMov "
          "(deberían estar corregidos por el parche).")

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM operaciones WHERE tipo_operacion ~* 'FCI' "
                    "AND concertacion >= %s", (desde,))
        sql_total = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM operaciones WHERE tipo_operacion ~* 'FCI' "
                    "AND (bruto = 0 OR bruto IS NULL) AND concertacion >= %s", (desde,))
        sql_cero = cur.fetchone()[0]
    print(f"\n== SQL operaciones (FCI, últimos {_DIAS}d) ==")
    print(f"  total FCI: {sql_total}   con bruto=0/null: {sql_cero}")

    print("\nLECTURA:")
    print(f"  - Mongo bruto=0 CON importe en NegocioMov ({con_importe} en la muestra) → el PARCHE no corrigió.")
    print(f"  - Si Mongo ~OK pero SQL bruto=0={sql_cero} alto → el SYNC no re-propagó (ingestado_en no se bumpea).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
