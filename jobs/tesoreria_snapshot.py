"""jobs/tesoreria_snapshot.py — foto diaria de la grilla BANCOS de Tesorería.

La vista de Tesorería es casi toda LIVE contra Aunesa y no se persiste: pasado el día
no hay forma de reconstruir lo que mostró la pantalla (Aunesa puede cambiar estados
hacia atrás, y lo cargado a mano se puede editar). Este job congela, al cierre del
día, las filas de la grilla BANCOS + los movimientos que componen cada celda.

UNA foto por día (`fecha` es única): correrlo dos veces actualiza la del día en vez de
acumular. TTL de 30 fechas, que se aplica solo al insertar.

Uso:
    python -m jobs.tesoreria_snapshot            # foto de HOY (ART) — cron
    python -m jobs.tesoreria_snapshot --fecha 2026-08-05
    python -m jobs.tesoreria_snapshot --dias 5   # rellena los últimos 5 días
"""
from __future__ import annotations

import argparse
import time
from datetime import timedelta

from api.services import tesoreria as tes
from core.job_runs import JobRunLogger

PAUSA_S = 0.5  # throttle entre días: rellenar varios no puede martillar Aunesa


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fecha", help="ISO YYYY-MM-DD (default: hoy ART)")
    ap.add_argument("--dias", type=int, default=1,
                    help="cuántos días hacia atrás fotografiar (default 1 = solo hoy)")
    args = ap.parse_args()

    hoy = tes._hoy_art().date()
    if args.fecha:
        dias = [tes._dia(args.fecha)]
    else:
        dias = sorted(hoy - timedelta(days=i) for i in range(max(1, args.dias)))

    with JobRunLogger("tesoreria_snapshot") as run:
        ok = 0
        for i, dia in enumerate(dias):
            if i:
                time.sleep(PAUSA_S)
            try:
                # origen='cron' salta el chequeo de allowlist (no hay usuario detrás).
                r = tes.tomar_snapshot(fecha=dia.isoformat(), actor="cron", origen="cron")
                ok += 1
                run.log(f"{dia}: {r['n_bancos']} bancos · {r['n_celdas']} celdas · "
                        f"{r['bytes']} bytes · purgadas {r['purgadas']}")
            except Exception as e:
                run.log(f"{dia}: ERROR {type(e).__name__}: {e}")
        run.set_stat("dias", len(dias))
        run.set_stat("fotos", ok)


if __name__ == "__main__":
    main()
