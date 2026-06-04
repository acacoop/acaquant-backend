"""scripts/backfill_fci_bruto.py — repara el bruto de TODAS las suscripciones FCI
viejas en $0, de forma SEGURA (REGLA #4): por ventanas de fecha indexadas + sleep.

Por qué no usar `fci_bilateral --full`: re-lee TODA la historia sin índice
(COLLSCAN de NegocioMovimientos ~338k + Operaciones 488k) → lento y tira el CPU
del M10. Esto hace LO MISMO (corrige el bruto desde NegocioMov por boleto) pero
recorre la historia por VENTANAS de N días: cada ventana filtra por `fecha` (usa
el índice fecha_categoria → lee solo esa ventana) y duerme `sleep` entre ventanas,
así jamás compite con los motores. Cobertura total, cero spikes.

UPDATE puro por boleto (el doc ya existe vía API) → NUNCA inserta → no duplica
(además de los índices únicos). Idempotente: cortable y re-corrible.

Correr vía run_job.sh (lock + timeout), idealmente fuera de rueda:
    /root/TradingAV/deploy/run_job.sh backfill_fci_bruto 40m \
        'cd /root/TradingAV && /root/TradingAV/venv/bin/python -m scripts.backfill_fci_bruto'
    python -m scripts.backfill_fci_bruto --dry-run    # no escribe, estima
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta

from pymongo import UpdateOne

from core.mongo import get_mongo_client

_CATS = ("suscripcion_fci", "rescate_fci")  # FCI normal (comprobante BOL)


def run(dry: bool, ventana: int, sleep_s: float) -> dict:
    db = get_mongo_client()["CashFlow"]
    mov, ops = db["NegocioMovimientos"], db["Operaciones"]

    fmin = ops.find_one({"concertacion": {"$ne": None}}, sort=[("concertacion", 1)],
                        projection={"_id": 0, "concertacion": 1})
    fmax = ops.find_one({"concertacion": {"$ne": None}}, sort=[("concertacion", -1)],
                        projection={"_id": 0, "concertacion": 1})
    if not fmin or not fmax:
        print("Sin fechas en Operaciones.")
        return {"ventanas": 0, "corregidos": 0}
    d0 = datetime.fromisoformat(fmin["concertacion"][:10]).date()
    d1 = datetime.fromisoformat(fmax["concertacion"][:10]).date()
    print(f"Rango: {d0} … {d1} · ventana {ventana}d · sleep {sleep_s}s"
          + ("  [DRY-RUN]" if dry else ""))

    total_corr = ventanas = 0
    cur = d1
    while cur >= d0:
        w_ini = max(d0, cur - timedelta(days=ventana - 1))
        # FCI BOL en la ventana → índice fecha_categoria (barato). El regex ^BOL
        # queda como filtro residual sobre los pocos docs de la ventana.
        q = {"fecha": {"$gte": w_ini.isoformat(), "$lte": cur.isoformat()},
             "categoria": {"$in": list(_CATS)},
             "comprobante": {"$regex": "^BOL", "$options": "i"}}
        imp_por_boleto = {
            str(d["comprobante"]).strip(): abs(d["importe"])
            for d in mov.find(q, {"_id": 0, "comprobante": 1, "importe": 1})
            if d.get("comprobante") and d.get("importe") is not None
        }
        if imp_por_boleto:
            # SOLO los REALMENTE rotos en Operaciones (bruto 0/null) → el dry-run no
            # cuenta de más (los rescates ya vienen con bruto OK y no matchean).
            rotos = [
                str(o["boleto"]).strip()
                for o in ops.find(
                    {"boleto": {"$in": list(imp_por_boleto)}, "bruto": {"$in": [0, None]}},
                    {"_id": 0, "boleto": 1})
            ]
            corr = len(rotos)
            if rotos and not dry:
                bulk = [UpdateOne({"boleto": b, "bruto": {"$in": [0, None]}},
                                  {"$set": {"bruto": imp_por_boleto[b]}})  # sin upsert
                        for b in rotos]
                corr = ops.bulk_write(bulk, ordered=False).modified_count
            total_corr += corr
            if corr:
                print(f"  {w_ini}…{cur}: {corr} corregidos")
        ventanas += 1
        cur = w_ini - timedelta(days=1)
        if cur >= d0:
            time.sleep(sleep_s)  # throttle: deja respirar al CPU entre ventanas
    print(f"\n→ {ventanas} ventanas · {total_corr} suscripciones "
          f"{'estimadas (DRY)' if dry else 'corregidas'}")
    return {"ventanas": ventanas, "corregidos": total_corr}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no escribe, estima")
    ap.add_argument("--ventana", type=int, default=30, help="días por ventana (default 30)")
    ap.add_argument("--sleep", type=float, default=1.5, help="pausa entre ventanas en seg (default 1.5)")
    args = ap.parse_args()
    res = run(args.dry_run, args.ventana, args.sleep)
    print(f"→ {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
