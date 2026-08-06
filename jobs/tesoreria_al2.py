"""jobs/tesoreria_al2.py — movimientos del banco AL2 (FERSI SA) a SQL.

SOLO AL2. La tesorería en general NO se persiste: la vista del día sigue siendo live
contra Aunesa. Lo único que no se puede resolver live es la SERIE de la tab SALDO AL2
(60 días, y Aunesa se pide día por día), así que se guardan esos movimientos —y nada
más— en `operaciones.tesoreria_al2`.

Idempotente: upsert por `id` de Aunesa. Se re-ingestan los últimos días para capturar
los cambios de estado (Pendiente → Procesado) de las filas ya guardadas.

Uso:
    python -m jobs.tesoreria_al2                # últimos 3 días (cron)
    python -m jobs.tesoreria_al2 --dias 60      # backfill inicial
    python -m jobs.tesoreria_al2 --dias 15 --dry-run   # no escribe; lista los bancos
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
                    help="no escribe; lista TODOS los bancos que trae Aunesa para "
                         "confirmar el string exacto del banco AL2")
    args = ap.parse_args()

    hoy = tes._hoy_art().date()
    dias = sorted(hoy - timedelta(days=i) for i in range(max(1, args.dias)))

    with JobRunLogger("tesoreria_al2") as run:
        total, bancos = 0, Counter()
        for i, dia in enumerate(dias):
            if i:
                time.sleep(PAUSA_S)
            try:
                if args.dry_run:
                    yyyymmdd = dia.strftime("%Y%m%d")
                    movs = [tes.aplanar(r, yyyymmdd)
                            for r in tes.traer_crudas(dia, tes.TODOS_ESTADOS) if r.get("id")]
                    for m in movs:
                        bancos[str(m.get("banco") or "—")] += 1
                    n_al2 = sum(1 for m in movs if tes.es_al2(m))
                    run.log(f"{dia}: {len(movs)} movimientos · {n_al2} AL2")
                    continue
                filas = tes.preparar_dia(dia)
                n = tes.persistir(filas)
                total += n
                if filas:
                    run.log(f"{dia}: {n} movimientos AL2")
            except Exception as e:
                run.error(f"{dia}: {str(e).splitlines()[0][:200]}")

        run.set_stat("dias", len(dias))
        run.set_stat("upserts", total)
        if args.dry_run:
            run.log("bancos vistos: " + ", ".join(f"{b} ({n})" for b, n in bancos.most_common(30)))
            run.log(f"DRY-RUN — no se escribió nada. AL2 = banco {tes.AL2_BANCO_CODIGO}")


if __name__ == "__main__":
    main()
