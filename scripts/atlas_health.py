"""scripts/atlas_health.py — salud del M10 desde la Atlas Admin API (read-only).

Imprime el CPU por nodo y las slow queries recientes — SOLO METADATOS (colección,
planSummary, docsExamined, duración). NUNCA el comando ni valores (montos, nombres):
la redacción es por diseño en core.atlas_api.slow_queries_meta. No toca el cluster.

Paso de MEDIR antes de cablear el watchdog: corré esto para confirmar que la API
responde y ver la salud. (Slow queries necesita el permiso 'Data Access Read Only'
en la API key; sin eso, el CPU igual funciona.)

    python -m scripts.atlas_health
    python -m scripts.atlas_health --min 30
"""
from __future__ import annotations

import argparse
import time

from dotenv import load_dotenv

from core import atlas_api

load_dotenv()  # ATLAS_* del .env (mismo patrón que los jobs)


def _linea(m: dict) -> str:
    ns = m.get("ns", "?")
    plan = m.get("planSummary", "?")
    dex = m.get("docsExamined")
    ret = m.get("nreturned", m.get("nReturned"))
    dur = m.get("durationMillis", m.get("millis"))
    ratio = f"{dex:,}:{ret}" if dex is not None and ret is not None else (f"{dex:,}" if dex is not None else "?")
    flag = "  🔴 COLLSCAN" if plan == "COLLSCAN" else ""
    app = f"  [{m['appName']}]" if m.get("appName") else ""
    return f"{ns:34} {plan:10} examined:returned={ratio:>16} {dur}ms{app}{flag}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=15, help="ventana de slow queries (min)")
    args = ap.parse_args()

    print("══ CPU por nodo (Atlas Admin API) ══")
    nodos = atlas_api.cpu_por_nodo()
    for n in nodos:
        cpu = n["cpu_pct"]
        marca = "  🔴" if (cpu or 0) >= 80 else ("  🟡" if (cpu or 0) >= 60 else "")
        print(f"  {n['alias']:48} {n['tipo']:18} CPU={cpu if cpu is not None else '—'}%{marca}")

    print(f"\n══ Slow queries (últimos {args.min} min) — SOLO metadatos ══")
    since_ms = int((time.time() - args.min * 60) * 1000)
    total = 0
    for n in nodos:
        try:
            sq = atlas_api.slow_queries_meta(n["id"], since_ms=since_ms)
        except Exception as e:
            print(f"  {n['alias'].split('.')[0]}: sin acceso a slowQueryLogs ({str(e)[:80]})")
            continue
        if not sq:
            continue
        print(f"  ── {n['alias'].split('.')[0]} ({len(sq)}) ──")
        for m in sq[:15]:
            print(f"    {_linea(m)}")
        total += len(sq)
    if not total:
        print("  (sin slow queries en la ventana — cluster tranquilo ✓, o falta el permiso)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
