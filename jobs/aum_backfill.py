"""
aum_backfill.py — Corre el snapshot de AuM para una fecha pasada.

Calcula el T+2 correcto para esa fecha (el que se habría usado ese día) y
lo pasa a la API de Aunesa. El resultado se guarda en Valuaciones.AuM con
fecha_snapshot = la fecha indicada.

Si la fecha ya tiene datos en AuM, los pisa por (id_cuenta, unidad,
fecha_snapshot).

Uso:
    python -m jobs.aum_backfill 2025-07-01
    python -m jobs.aum_backfill 2025-07-01 --workers 4 --timeout 240 --retries 3
    python -m jobs.aum_backfill 2025-07-01 --cuenta 805,128
    python -m jobs.aum_backfill 2025-07-01 --desde 03/07/2025   # override manual

Flags útiles cuando Aunesa anda lenta para data histórica:
    --workers N      paralelismo (default 4; bajá a 2-3 si timeoutea)
    --timeout S      timeout HTTP por cuenta (default 240s; subí a 360+ si hace falta)
    --retries N      reintentos por cuenta con backoff (default 3)
    --cuenta IDS     CSV de id_cuenta — backfilea solo esas (útil para
                     reintentar las que fallaron sin tocar las OK)
"""

import argparse
import os
import sys
import threading
import time
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


def _parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("fecha", help="fecha_snapshot YYYY-MM-DD")
    ap.add_argument("--desde", help="Override de `desde` (DD/MM/YYYY). "
                                    "Default: T+2 calculado.")
    ap.add_argument("--workers", type=int, default=4,
                    help="Paralelismo de cuentas (default 4)")
    ap.add_argument("--timeout", type=int, default=240,
                    help="Timeout HTTP por cuenta en segundos (default 240)")
    ap.add_argument("--retries", type=int, default=3,
                    help="Reintentos por cuenta antes de marcar como fallida (default 3)")
    ap.add_argument("--cuenta",
                    help="CSV de id_cuenta a backfilear (default: todas las activas)")
    return ap.parse_args()


def main():
    args = _parse_args()

    fecha_str = args.fecha
    try:
        fecha_dt = datetime.strptime(fecha_str, "%Y-%m-%d")
    except ValueError:
        print(f"Formato de fecha inválido: {fecha_str}. Usar YYYY-MM-DD.")
        sys.exit(1)

    fecha_snapshot = fecha_str
    timestamp      = fecha_dt.replace(hour=23, minute=0, second=0)

    if args.desde:
        desde = args.desde
        print(f"📅 fecha_snapshot: {fecha_snapshot} | desde (manual): {desde}")
    else:
        desde = t2_para_fecha(fecha_dt)
        print(f"📅 fecha_snapshot: {fecha_snapshot} | desde (T+2): {desde}")

    print(f"⚙  workers={args.workers}  timeout={args.timeout}s  retries={args.retries}")
    print("\n🔑 Autenticando...", flush=True)
    headers_ref  = autenticar()
    headers_lock = threading.Lock()
    print("✅ Auth OK\n", flush=True)

    cuentas = obtener_cuentas(headers_ref)

    # Filtro --cuenta: backfilear solo las que pediste.
    if args.cuenta:
        wanted = {c.strip() for c in args.cuenta.split(",") if c.strip()}
        cuentas = cuentas[cuentas["id"].astype(str).isin(wanted)].reset_index(drop=True)
        print(f"📋 {len(cuentas)} cuentas filtradas (--cuenta {args.cuenta})\n", flush=True)
    else:
        print(f"📋 {len(cuentas)} cuentas activas\n", flush=True)

    total = len(cuentas)
    if total == 0:
        print("Sin cuentas que procesar.")
        return

    client = get_mongo_client()
    col    = client["Valuaciones"]["AuM"]
    registros_total = 0
    fallidas: list[tuple[str, str, str]] = []  # (id, denom, error)

    def _consultar_con_retry(cid, den, idx):
        last_err: str = ""
        for intento in range(1, args.retries + 1):
            try:
                with headers_lock:
                    h = dict(headers_ref)
                data, necesita_reauth = consultar_posicion(cid, h, desde, timeout=args.timeout)
                if necesita_reauth:
                    with headers_lock:
                        nuevos = autenticar()
                        headers_ref.clear()
                        headers_ref.update(nuevos)
                        h = dict(headers_ref)
                    data, _ = consultar_posicion(cid, h, desde, timeout=args.timeout)
                if not data:
                    print(f"[{idx+1:3d}/{total}] [{cid}] {den[:40]} → sin datos", flush=True)
                    return []
                registros = procesar(data, fecha_snapshot, timestamp)
                tag = f" (intento {intento})" if intento > 1 else ""
                print(f"[{idx+1:3d}/{total}] [{cid}] {den[:40]} → {len(registros)} pos{tag}", flush=True)
                return registros
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                if intento < args.retries:
                    backoff = 2 ** intento  # 2s, 4s, 8s, ...
                    print(f"[{idx+1:3d}/{total}] [{cid}] ⚠ intento {intento} falló ({last_err}); reintentando en {backoff}s", flush=True)
                    time.sleep(backoff)
                else:
                    print(f"[{idx+1:3d}/{total}] [{cid}] ❌ tras {args.retries} intentos: {last_err}", flush=True)
        fallidas.append((cid, den, last_err))
        return []

    futures_map = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for i, row in cuentas.iterrows():
            cuenta_id    = str(row["id"])
            denominacion = row["denominacion"]
            futures_map[executor.submit(
                _consultar_con_retry, cuenta_id, denominacion, i,
            )] = cuenta_id

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

    if fallidas:
        print(f"\n⚠ {len(fallidas)} cuentas FALLIDAS (timeout o error):")
        for cid, den, err in fallidas:
            print(f"  [{cid}] {den[:50]:50s}  {err}")
        ids = ",".join(c[0] for c in fallidas)
        print("\nReintento sugerido (solo las fallidas, sin paralelismo y timeout largo):")
        print(f"  python -m jobs.aum_backfill {fecha_snapshot} --cuenta {ids} --workers 1 --timeout 360 --retries 5")

    unidades = col.distinct("unidad", {"fecha_snapshot": fecha_snapshot})
    _sincronizar_assets(client["Valuaciones"]["Assets"], unidades)
    print(f"✅ Assets sincronizado: {len(unidades)} unidades.")

    sync_carteras_ii()

    from jobs.aum_resumen_fci import sync_fecha as sync_resumen_fci
    sync_resumen_fci(client, fecha_snapshot)


if __name__ == "__main__":
    main()
