"""scripts/atlas_health.py — salud del M10 desde la Atlas Admin API (read-only).

Prueba el lector core/atlas_api: imprime el CPU por nodo y las slow queries
recientes (lo mismo que el Query Profiler, por código). NO toca el cluster — lee
la API REST de gestión.

Es el paso de MEDIR antes de cablear el watchdog: corré esto primero para confirmar
que la API responde y ver la forma real de los datos. Recién después automatizamos.

    python -m scripts.atlas_health
    python -m scripts.atlas_health --min 30     # slow queries de los últimos 30 min
"""
from __future__ import annotations

import argparse
import json
import time

from core import atlas_api


def _resumen_query(item: dict) -> str:
    """Intenta extraer lo importante de un slow query log (forma defensiva)."""
    line = item.get("line")
    if isinstance(line, str):
        try:
            line = json.loads(line)
        except (ValueError, TypeError):
            return str(item.get("line"))[:160]
    attr = (line or {}).get("attr") or line or {}
    ns = attr.get("ns", "?")
    plan = attr.get("planSummary", "?")
    dex = attr.get("docsExamined")
    ret = attr.get("nreturned", attr.get("nReturned"))
    dur = attr.get("durationMillis", attr.get("millis"))
    ratio = f"{dex}/{ret}" if dex is not None and ret not in (None, 0) else (str(dex) if dex is not None else "?")
    flag = "  🔴 COLLSCAN" if plan == "COLLSCAN" else ""
    return f"{ns:34} {plan:10} examined:returned={ratio:>14} {dur}ms{flag}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=15, help="ventana de slow queries (min)")
    ap.add_argument("--raw", action="store_true", help="imprime el JSON crudo de una slow query")
    args = ap.parse_args()

    print("══ CPU por nodo (Atlas Admin API) ══")
    nodos = atlas_api.cpu_por_nodo()
    for n in nodos:
        cpu = n["cpu_pct"]
        marca = "  🔴" if (cpu or 0) >= 80 else ("  🟡" if (cpu or 0) >= 60 else "")
        print(f"  {n['alias']:30} {n['tipo']:18} CPU={cpu if cpu is not None else '—'}%{marca}")

    print(f"\n══ Slow queries (últimos {args.min} min) ══")
    since_ms = int((time.time() - args.min * 60) * 1000)
    total = 0
    for n in nodos:
        try:
            sq = atlas_api.slow_queries(n["id"], since_ms=since_ms)
        except Exception as e:
            print(f"  {n['alias']}: no se pudo leer slowQueryLogs ({e})")
            continue
        if not sq:
            continue
        print(f"  ── {n['alias']} ({len(sq)} slow queries) ──")
        if args.raw and sq:
            print("  [RAW primera query]:")
            print(json.dumps(sq[0], indent=2)[:1500])
        for item in sq[:15]:
            print(f"    {_resumen_query(item)}")
        total += len(sq)
    if not total:
        print("  (sin slow queries en la ventana — o el cluster está tranquilo ✓)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
