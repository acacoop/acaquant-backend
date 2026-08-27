"""scripts/diag_ap5_corrida.py — ¿el job de AP5 corrió hoy, y qué escribió?

READ-ONLY. Contesta con DATOS, no con lo que dice el crontab.

⚠️ **Un cron que no dispara no deja ningún rastro**, y la vista tampoco: si el
job no corrió, quedan los datos del día anterior — las mismas filas, los mismos
totales, cero señales. «Miré y estaba todo igual» y «el job murió» se ven
exactamente igual desde la pantalla.

Este diag mira las tres cosas que lo separan, y las mira **en la base**:

  1. `manager.job_runs` — qué corridas quedaron registradas y con qué resultado.
     Es el único lugar donde una corrida que falló deja huella.
  2. Qué día hábil DEBERÍA tener escrito (lo calcula el mismo código que el job).
  3. Qué días hay de verdad en `ap5.portfolio` y `ap5.margenes`.

Si (2) no está en (3), el job no escribió lo de hoy — y (1) dice si fue porque
no corrió o porque corrió y falló, que son dos problemas distintos.

Uso:
    python -m scripts.diag_ap5_corrida
    python -m scripts.diag_ap5_corrida --dias 7
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime

from core.postgres import get_pool
from jobs.ap5_portfolio import TIPO, ultimo_dia_habil


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        if cur.description is None:
            return []
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser(description="¿Corrió el job de AP5? (read-only)")
    ap.add_argument("--dias", type=int, default=5, help="cuántas corridas mirar")
    args = ap.parse_args()

    hoy = date.today()
    esperado = ultimo_dia_habil()
    esperado_iso = f"{esperado[:4]}-{esperado[4:6]}-{esperado[6:]}"

    print("=" * 78)
    print(f"AP5 · ¿corrió el job?   hoy {hoy} ({hoy.strftime('%A')})")
    print("=" * 78)
    print("  El job pide SIEMPRE el último día hábil → deberia haber escrito")
    print(f"  business_date = {esperado_iso}")

    # ── 1) las corridas registradas ───────────────────────────────────────
    print("\n" + "-" * 78)
    print(f"1) manager.job_runs · tipo = {TIPO}")
    print("-" * 78)
    runs = _q(
        "SELECT started_at, finished_at, status, data FROM manager.job_runs "
        "WHERE tipo = %(t)s ORDER BY started_at DESC LIMIT %(n)s",
        {"t": TIPO, "n": args.dias},
    )
    if not runs:
        print("  ⚠️ NINGUNA corrida registrada. El job nunca se ejecutó, o nunca")
        print("     llegó a abrir el JobRunLogger (falla de import, venv, cron).")
    for r in runs:
        ini = r["started_at"]
        d = r["data"] if isinstance(r["data"], dict) else json.loads(r["data"] or "{}")
        st = d.get("stats") or {}
        marca = "  ← HOY" if ini and ini.date() == hoy else ""
        print(f"  {ini:%Y-%m-%d %H:%M UTC}  {r['status']!s:<8} "
              f"fecha={st.get('fecha', '?')}  filas={st.get('filas_escritas', '?')}  "
              f"margenes={st.get('margenes_escritas', '?')}{marca}")
        for e in (d.get("errors") or [])[:3]:
            print(f"      ✗ {str(e)[:110]}")

    corrio_hoy = any(r["started_at"] and r["started_at"].date() == hoy for r in runs)
    print(f"\n  → ¿hay una corrida de HOY?  {'SÍ' if corrio_hoy else 'NO'}")

    # ── 2) qué días hay realmente escritos ────────────────────────────────
    for tabla, col in (("ap5.portfolio", "business_date"), ("ap5.margenes", "fecha")):
        print("\n" + "-" * 78)
        print(f"2) {tabla} · últimos días con datos")
        print("-" * 78)
        filas = _q(
            f"SELECT to_char({col}, 'YYYY-MM-DD') AS dia, count(*) AS n, "
            f"       max(actualizado_at) AS escrito "
            f"FROM {tabla} GROUP BY 1 ORDER BY 1 DESC LIMIT %(n)s",
            {"n": args.dias},
        )
        if not filas:
            print("  (vacía)")
            continue
        for f in filas:
            esc: datetime | None = f["escrito"]
            marca = "  ← el que se esperaba hoy" if f["dia"] == esperado_iso else ""
            cuando = f"{esc:%d/%m %H:%M UTC}" if esc else "?"
            print(f"  {f['dia']}  {f['n']:>6} filas   escrito {cuando}{marca}")
        if not any(f["dia"] == esperado_iso for f in filas):
            print(f"\n  ⚠️ {esperado_iso} NO está en {tabla}.")

    print("\n" + "=" * 78)
    print("Cómo leerlo:")
    print("  · corrida de hoy SÍ + el día esperado presente  → todo bien.")
    print("  · corrida de hoy NO                             → el cron no disparó:")
    print("    `crontab -l | grep ap5` en el Droplet (instalarlo no lo hace el deploy).")
    print("  · corrida de hoy SÍ + status error              → corrió y falló:")
    print("    el motivo está arriba, en la línea del ✗.")


if __name__ == "__main__":
    main()
