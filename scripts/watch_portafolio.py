"""scripts/watch_portafolio.py — monitor EN VIVO del backfill de portafolio (lee SQL).

Refresca cada 5s y muestra, por fecha: OK / vacía / TIMEOUT / ERROR (de
portafolio.backfill_log) + filas reales en portafolio.tenencia. No depende del
log del proceso — lee la base directo, que es la fuente de verdad.

Uso:
    python -m scripts.watch_portafolio
    python -m scripts.watch_portafolio --once       # imprime una vez y sale
    python -m scripts.watch_portafolio --cada 10     # refresca cada 10s
"""
from __future__ import annotations

import sys
import time

from core.postgres import get_pool


def _opt(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _snapshot():
    """Devuelve dict por fecha: {ok,vacia,timeout,error,filas}."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT fecha::text, status, count(*) "
                    "FROM portafolio.backfill_log GROUP BY fecha, status")
        log = cur.fetchall()
        cur.execute("SELECT fecha::text, count(*) FROM portafolio.tenencia GROUP BY fecha")
        ten = dict(cur.fetchall())

    por_fecha: dict[str, dict] = {}
    for fecha, status, n in log:
        d = por_fecha.setdefault(fecha, {"ok": 0, "vacia": 0, "timeout": 0, "error": 0})
        if status == "ok":
            d["ok"] += n
        elif status == "vacia":
            d["vacia"] += n
        elif status == "timeout":
            d["timeout"] += n
        else:
            d["error"] += n
    for fecha in set(ten) | set(por_fecha):
        por_fecha.setdefault(fecha, {"ok": 0, "vacia": 0, "timeout": 0, "error": 0})
        por_fecha[fecha]["filas"] = ten.get(fecha, 0)
    return por_fecha


def _render(por_fecha: dict, limpiar: bool = True):
    if limpiar:
        print("\033[2J\033[H", end="")  # limpia pantalla (solo modo vivo)
    print("═" * 78)
    print("  MONITOR BACKFILL portafolio.tenencia  ·  refresca cada ciclo  ·  Ctrl-C corta")
    print("═" * 78)
    print(f"  {'FECHA':<12} {'OK':>6} {'VACÍA':>7} {'TIMEOUT':>8} {'ERROR':>7} {'FILAS SQL':>12}")
    print("  " + "-" * 60)
    tot = {"ok": 0, "vacia": 0, "timeout": 0, "error": 0, "filas": 0}
    for fecha in sorted(por_fecha):
        d = por_fecha[fecha]
        for k in tot:
            tot[k] += d.get(k, 0)
        alerta = "  ⚠️" if (d["timeout"] or d["error"]) else ""
        print(f"  {fecha:<12} {d['ok']:>6} {d['vacia']:>7} {d['timeout']:>8} "
              f"{d['error']:>7} {d.get('filas', 0):>12,}{alerta}")
    print("  " + "-" * 60)
    print(f"  {'TOTAL':<12} {tot['ok']:>6} {tot['vacia']:>7} {tot['timeout']:>8} "
          f"{tot['error']:>7} {tot['filas']:>12,}")
    print("═" * 78)


def main() -> int:
    cada = int(_opt("--cada", 5))
    once = "--once" in sys.argv
    while True:
        try:
            _render(_snapshot())
        except Exception as e:
            print(f"(error leyendo SQL: {type(e).__name__}: {e})")
        if once:
            return 0
        time.sleep(cada)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n(monitor cortado)")
