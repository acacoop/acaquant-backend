"""
backfill_aum.py — Corre el snapshot de AuM para una fecha pasada específica.

Calcula el T+2 correcto para esa fecha (el que se habría usado ese día)
y lo pasa a la API de Aunesa. El resultado se guarda en Valuaciones.AuM
con fecha_snapshot = la fecha indicada.

Uso:
    python backfill_aum.py 2026-03-24
    python backfill_aum.py 2026-03-24 2026-03-26  # fecha_snapshot, desde_override

Si la fecha ya tiene datos en AuM, los pisa (upsert por id_cuenta+unidad+fecha_snapshot).
"""

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import holidays
from pymongo import UpdateOne

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.mongo import get_mongo_client
from jobs.aum import (
    _sincronizar_assets,
    autenticar,
    consultar_posicion,
    obtener_cuentas,
    procesar,
    sync_carteras_ii,
)


def t2_para_fecha(fecha_dt):
    """Calcula el T+2 hábil a partir de una fecha dada (como si corriéramos ese día)."""
    arg_holidays = holidays.Argentina()

    def proximo_habil(d):
        d += timedelta(days=1)
        while d.weekday() >= 5 or d in arg_holidays:
            d += timedelta(days=1)
        return d

    t1 = proximo_habil(fecha_dt)
    t2 = proximo_habil(t1)
    return t2.strftime("%d/%m/%Y")


def main():
    if len(sys.argv) < 2:
        print("Uso: python backfill_aum.py YYYY-MM-DD [desde_override DD/MM/YYYY]")
        sys.exit(1)

    fecha_str = sys.argv[1]  # ej: "2026-03-24"
    try:
        fecha_dt = datetime.strptime(fecha_str, "%Y-%m-%d")
    except ValueError:
        print(f"Formato de fecha inválido: {fecha_str}. Usar YYYY-MM-DD.")
        sys.exit(1)

    fecha_snapshot = fecha_str  # lo que se guarda en Mongo
    timestamp      = fecha_dt.replace(hour=23, minute=0, second=0)

    if len(sys.argv) >= 3:
        desde = sys.argv[2]  # override manual
        print(f"📅 fecha_snapshot: {fecha_snapshot} | desde (manual): {desde}")
    else:
        desde = t2_para_fecha(fecha_dt)
        print(f"📅 fecha_snapshot: {fecha_snapshot} | desde (T+2 calculado): {desde}")

    print("\n🔑 Autenticando...", flush=True)
    headers_ref  = autenticar()
    headers_lock = threading.Lock()
    print("✅ Auth OK\n", flush=True)

    cuentas = obtener_cuentas(headers_ref)
    total   = len(cuentas)
    print(f"📋 {total} cuentas activas\n", flush=True)

    client = get_mongo_client()
    col    = client["Valuaciones"]["AuM"]
    registros_total = 0

    futures_map = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        for i, row in cuentas.iterrows():
            cuenta_id    = str(row["id"])
            denominacion = row["denominacion"]

            def _worker(cid=cuenta_id, den=denominacion, idx=i):
                try:
                    with headers_lock:
                        h = dict(headers_ref)
                    data, necesita_reauth = consultar_posicion(cid, h, desde)
                    if necesita_reauth:
                        with headers_lock:
                            nuevos = autenticar()
                            headers_ref.clear()
                            headers_ref.update(nuevos)
                            h = dict(headers_ref)
                        data, _ = consultar_posicion(cid, h, desde)
                    if not data:
                        print(f"[{idx+1:3d}/{total}] [{cid}] {den[:40]} → sin datos", flush=True)
                        return []
                    registros = procesar(data, fecha_snapshot, timestamp)
                    print(f"[{idx+1:3d}/{total}] [{cid}] {den[:40]} → {len(registros)} posiciones", flush=True)
                    return registros
                except Exception as e:
                    print(f"[{idx+1:3d}/{total}] [{cid}] ❌ {e}", flush=True)
                    return []

            futures_map[executor.submit(_worker)] = cuenta_id

        for f in as_completed(futures_map):
            registros = f.result()
            if not registros:
                continue
            ops = [
                UpdateOne(
                    {"id_cuenta": r["id_cuenta"], "unidad": r["unidad"], "fecha_snapshot": r["fecha_snapshot"]},
                    {"$set": r},
                    upsert=True,
                )
                for r in registros
            ]
            col.bulk_write(ops, ordered=False)
            registros_total += len(registros)

    print(f"\n✅ {registros_total} registros insertados para {fecha_snapshot}")

    unidades = col.distinct("unidad", {"fecha_snapshot": fecha_snapshot})
    _sincronizar_assets(client["Valuaciones"]["Assets"], unidades)
    print(f"✅ Assets sincronizado: {len(unidades)} unidades.")

    # Refrescar CarterasII (basado en hoy, no en la fecha backfilleada)
    sync_carteras_ii()


if __name__ == "__main__":
    main()
