"""Backfill AuM ULTRA RÁPIDO para una lista chica de cuentas hardcodeadas.

Optimización vs `jobs.aum_backfill`:
  1. Salta el listado inicial de 1796 cuentas (Aunesa request lenta) — usa
     la lista hardcodeada directo.
  2. Paraleliza 1 worker por cuenta (concurrencia máxima): si pedís 10
     cuentas, las 10 se piden a Aunesa al mismo tiempo. Total ≈ tiempo de
     la cuenta más lenta.
  3. Logging por cuenta con timing real (segundos efectivos).

Editá `CUENTAS` abajo para cambiar la lista, o usá --cuenta para override.

Uso:
    python -m scripts.backfill_aum_quick 2025-07-01
    python -m scripts.backfill_aum_quick 2025-07-01 --cuenta 101,163,255
    python -m scripts.backfill_aum_quick 2025-07-01 --timeout 480 --retries 4
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from pymongo import UpdateOne

from core.mongo import get_mongo_client
from jobs.aum import (
    _sincronizar_assets,
    autenticar,
    consultar_posicion,
    procesar,
)
from jobs.aum_backfill import t2_para_fecha

# Cuentas de este backfill rápido (curado a mano — editar al cambiar la
# lista de las que necesitan re-llenado puntual).
CUENTAS: list[str] = [
    "101", "163", "255", "170", "176", "184", "194", "210", "455", "523",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fecha", help="fecha_snapshot YYYY-MM-DD")
    ap.add_argument("--desde",
                    help="Override DD/MM/YYYY del `desde` (default: T+2 calculado).")
    ap.add_argument("--cuenta",
                    help="Override CSV de cuentas (default: CUENTAS hardcodeada)")
    ap.add_argument("--timeout", type=int, default=360,
                    help="Timeout HTTP por cuenta (default 360s)")
    ap.add_argument("--retries", type=int, default=3,
                    help="Reintentos por cuenta con backoff (default 3)")
    args = ap.parse_args()

    fecha_str = args.fecha
    try:
        fecha_dt = datetime.strptime(fecha_str, "%Y-%m-%d")
    except ValueError:
        print(f"Formato inválido: {fecha_str}. Usar YYYY-MM-DD.")
        sys.exit(1)

    fecha_snapshot = fecha_str
    timestamp = fecha_dt.replace(hour=23, minute=0, second=0)
    desde = args.desde or t2_para_fecha(fecha_dt)

    cuentas = (
        [c.strip() for c in args.cuenta.split(",") if c.strip()]
        if args.cuenta else CUENTAS
    )
    workers = len(cuentas)  # 1 worker por cuenta = max concurrencia

    print(f"📅 {fecha_snapshot} | desde {desde}")
    print(f"⚡ {len(cuentas)} cuentas en paralelo "
          f"(workers={workers}, timeout={args.timeout}s, retries={args.retries})")
    print(f"   {','.join(cuentas)}")

    print("\n🔑 Autenticando…", flush=True)
    headers_ref = autenticar()
    headers_lock = threading.Lock()
    print("✅ Auth OK\n", flush=True)

    client = get_mongo_client()
    col = client["Valuaciones"]["AuM"]
    fallidas: list[tuple[str, str]] = []
    registros_total = 0

    def _worker(cid: str) -> list:
        last_err = ""
        for intento in range(1, args.retries + 1):
            try:
                with headers_lock:
                    h = dict(headers_ref)
                t0 = time.time()
                data, necesita_reauth = consultar_posicion(cid, h, desde, timeout=args.timeout)
                if necesita_reauth:
                    with headers_lock:
                        nuevos = autenticar()
                        headers_ref.clear()
                        headers_ref.update(nuevos)
                        h = dict(headers_ref)
                    data, _ = consultar_posicion(cid, h, desde, timeout=args.timeout)
                dt = time.time() - t0
                if not data:
                    print(f"[{cid}] sin datos ({dt:.1f}s)", flush=True)
                    return []
                registros = procesar(data, fecha_snapshot, timestamp)
                tag = f" (intento {intento})" if intento > 1 else ""
                print(f"[{cid}] {len(registros)} pos en {dt:.1f}s{tag}", flush=True)
                return registros
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                if intento < args.retries:
                    backoff = 2 ** intento
                    print(f"[{cid}] ⚠ intento {intento}: {last_err} → retry en {backoff}s",
                          flush=True)
                    time.sleep(backoff)
                else:
                    print(f"[{cid}] ❌ tras {args.retries} intentos: {last_err}",
                          flush=True)
        fallidas.append((cid, last_err))
        return []

    t_start = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_worker, cid) for cid in cuentas]
        for f in as_completed(futures):
            registros = f.result()
            if not registros:
                continue
            ops = [
                UpdateOne(
                    {
                        "id_cuenta":      r["id_cuenta"],
                        "unidad":         r["unidad"],
                        "fecha_snapshot": r["fecha_snapshot"],
                    },
                    {"$set": r},
                    upsert=True,
                )
                for r in registros
            ]
            col.bulk_write(ops, ordered=False)
            registros_total += len(registros)

    elapsed = time.time() - t_start
    print(f"\n✅ {registros_total} registros insertados en {elapsed:.1f}s "
          f"({len(cuentas) - len(fallidas)}/{len(cuentas)} cuentas OK)")
    if fallidas:
        print(f"\n⚠ {len(fallidas)} cuentas fallidas:")
        for cid, err in fallidas:
            print(f"  [{cid}] {err}")
        ids = ",".join(c[0] for c in fallidas)
        print("\nReintento sugerido:")
        print(f"  python -m scripts.backfill_aum_quick {fecha_snapshot} "
              f"--cuenta {ids} --timeout 480 --retries 5")
        return

    # Sync de assets nuevas (aunque sean pocas, para que las unidades
    # frescas que aparezcan en este snapshot estén dadas de alta en la
    # API copy).
    unidades = col.distinct(
        "unidad",
        {"fecha_snapshot": fecha_snapshot, "id_cuenta": {"$in": cuentas}},
    )
    _sincronizar_assets(client["TitulosAPI"]["AssetsAPI"], unidades)
    print(f"✅ AssetsAPI sincronizado ({len(unidades)} unidades).")

    # Refrescar el rollup FCI puntual para esta fecha — la vista AUM lo lee.
    from jobs.aum_resumen_fci import sync_fecha as sync_resumen_fci
    sync_resumen_fci(client, fecha_snapshot)
    print(f"✅ AuMResumenFCI[{fecha_snapshot}] refrescado.")


if __name__ == "__main__":
    main()
