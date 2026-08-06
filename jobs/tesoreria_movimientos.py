"""jobs/tesoreria_movimientos.py — histórico de movimientos bancarios (Aunesa) a SQL.

Persiste `cuentas/consultaMovDocsSolicitados` en `operaciones.tesoreria_movimientos`.
La vista TESORERÍA del día sigue siendo LIVE; esto existe porque la tab SALDO AL2
necesita SERIE (últimos 60 días del banco FERSI SA `[00001713]`) y Aunesa se pide día
por día — 60 llamadas por pantallazo era inviable.

Idempotente: upsert por `id` de Aunesa. Se re-ingestan siempre los últimos días para
capturar los cambios de estado (Pendiente → Procesado) de las filas ya guardadas.

Uso:
    python -m jobs.tesoreria_movimientos                # últimos 3 días (cron)
    python -m jobs.tesoreria_movimientos --dias 60      # backfill inicial
    python -m jobs.tesoreria_movimientos --dias 60 --dry-run
"""
from __future__ import annotations

import argparse
import time
from collections import Counter
from datetime import timedelta

from api.services import tesoreria as tes
from core.job_runs import JobRunLogger

PAUSA_S = 0.5  # throttle entre días: el backfill de 60 días no puede martillar Aunesa


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dias", type=int, default=3, help="días hacia atrás (default 3)")
    ap.add_argument("--dry-run", action="store_true",
                    help="no escribe; solo reporta qué bancos/estados trae Aunesa")
    args = ap.parse_args()

    hoy = tes._hoy_art().date()
    dias = [hoy - timedelta(days=i) for i in range(max(1, args.dias))]

    with JobRunLogger("tesoreria_movimientos") as run:
        total, bancos, sin_datos = 0, Counter(), 0
        for i, dia in enumerate(sorted(dias)):
            if i:
                time.sleep(PAUSA_S)
            try:
                filas = tes.preparar_dia(dia)
            except Exception as e:
                run.error(f"{dia}: {str(e).splitlines()[0][:200]}")
                continue
            if not filas:
                sin_datos += 1
                continue
            for f in filas:
                bancos[f["banco"] or "—"] += 1
            n = 0 if args.dry_run else tes.persistir(filas)
            total += n
            run.log(f"{dia}: {len(filas)} movimientos · {n} upserts")

        run.set_stat("dias", len(dias))
        run.set_stat("upserts", total)
        run.set_stat("dias_sin_datos", sin_datos)
        run.set_stat("bancos", dict(bancos.most_common(20)))
        # El inventario de bancos hace de diagnóstico: sirve para confirmar el string
        # exacto del banco de SALDO AL2 sin escribir un script aparte.
        run.log("bancos vistos: " + ", ".join(f"{b} ({n})" for b, n in bancos.most_common(20)))
        al2 = sum(n for b, n in bancos.items() if tes.AL2_BANCO_CODIGO in b)
        run.log(f"AL2 (banco {tes.AL2_BANCO_CODIGO}): {al2} movimientos en la ventana")
        if args.dry_run:
            run.log("DRY-RUN — no se escribió nada.")


if __name__ == "__main__":
    main()
