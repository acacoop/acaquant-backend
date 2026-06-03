"""scripts/diag_db_load.py — READ-ONLY: qué está exigiendo a Mongo AHORA.

Para diagnosticar el pico de CPU del cluster. Se conecta al PRIMARY (donde saltó
la alerta) y lista las operaciones ACTIVAS ordenadas por tiempo corriendo, marcando
los COLLSCAN (escaneos de colección completa = lo que suele clavar el CPU en M10).
También muestra conexiones y opcounters. NO escribe nada.

Necesita que el usuario de Mongo tenga el privilegio `inprog` (rol atlasAdmin /
clusterMonitor). Si no lo tiene, lo dice y te manda al panel Real-Time de Atlas.

Uso:
    python -m scripts.diag_db_load
    python -m scripts.diag_db_load --min-secs 1   # solo ops corriendo >= 1s
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


def _secs(op: dict) -> float:
    if op.get("secs_running") is not None:
        return float(op["secs_running"])
    micros = op.get("microsecs_running")
    return float(micros) / 1e6 if micros else 0.0


def _resumen_cmd(op: dict) -> str:
    """Namespace + tipo de comando, recortado para que entre en una línea."""
    cmd = op.get("command") or {}
    # El primer key del command suele ser el verbo (find/aggregate/update/...).
    verbo = next((k for k in cmd if not k.startswith("$")), op.get("op", "?"))
    detalle = ""
    if "filter" in cmd:
        detalle = f" filter={str(cmd['filter'])[:80]}"
    elif "pipeline" in cmd:
        detalle = f" pipeline[0]={str((cmd['pipeline'] or [{}])[0])[:80]}"
    return f"{verbo}{detalle}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-secs", type=float, default=0.0,
                    help="solo ops corriendo >= N segundos (default 0)")
    args = ap.parse_args()

    client = get_mongo_client()  # primary (rw)
    admin = client.admin

    print("=" * 72)
    print("DIAG carga Mongo — operaciones activas en el PRIMARY (read-only)")
    print("=" * 72)

    try:
        res = admin.command("currentOp", {"active": True})
    except Exception as e:
        print(f"\n⚠ No pude leer currentOp: {e}")
        print("  El usuario de Mongo no tiene privilegio `inprog`. Usá el panel")
        print("  Real-Time / Query Profiler de la consola de Atlas en su lugar.")
        return 1

    ops = res.get("inprog", [])
    # Sacamos las ops internas / idle (no aportan al CPU de queries).
    reales = [
        o for o in ops
        if o.get("op") not in (None, "none")
        and not (o.get("command") or {}).get("hello")
        and "currentOp" not in (o.get("command") or {})
        and _secs(o) >= args.min_secs
    ]
    reales.sort(key=_secs, reverse=True)

    print(f"\nOps activas (op != none): {len(reales)}"
          + (f"  (filtradas a >= {args.min_secs}s)" if args.min_secs else ""))

    collscans = [o for o in reales if "COLLSCAN" in str(o.get("planSummary") or "")]
    if collscans:
        print(f"⚠ {len(collscans)} de ellas son COLLSCAN (escaneo de colección completa)")

    print(f"\n{'secs':>7}  {'op':<9} {'plan':<10} {'ns':<32} app")
    print("-" * 72)
    for o in reales[:25]:
        plan = (o.get("planSummary") or "")[:10]
        ns = (o.get("ns") or "")[:32]
        app = (o.get("appName") or o.get("clientMetadata", {}).get("application", {}).get("name") or "")[:18]
        print(f"{_secs(o):>7.1f}  {(o.get('op') or '?'):<9} {plan:<10} {ns:<32} {app}")
        print(f"         └ {_resumen_cmd(o)}")

    # Contexto: conexiones + opcounters.
    try:
        ss = admin.command("serverStatus")
        conn = ss.get("connections", {})
        oc = ss.get("opcounters", {})
        print("\n── contexto ──────────────────────────────────────────────────")
        print(f"   conexiones: current={conn.get('current')} available={conn.get('available')}")
        print(f"   opcounters (acumulado desde el último restart): "
              f"query={oc.get('query')} update={oc.get('update')} "
              f"getmore={oc.get('getmore')} command={oc.get('command')}")
    except Exception as e:
        print(f"\n(no pude leer serverStatus: {e})")

    print("\nread-only: no se escribió nada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
