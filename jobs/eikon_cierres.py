"""jobs/eikon_cierres.py — persiste el CIERRE diario de los feeds Eikon nuevos
(soberanos offshore + futuros de Chicago) en `mercado.eikon_cierres`.

Para qué: los snapshots live (`eikon_bonos_snapshot` / `eikon_chicago_snapshot`)
son una foto sin memoria — sin esto, las columnas 7D/MTD/YTD de los bonos OFF en
la watchlist quedan en "—" para siempre y Chicago no puede graficarse. Con unos
días de corridas, `api/services/argy.py` calcula los retornos contra estos
cierres (mismo patrón de anchors que MEP/CCL).

Regla anti-dato-viejo: solo persiste filas cuyo snapshot se actualizó HOY (ART).
Si el feed no se prendió en el día, no se escribe nada (no fabricamos un
"cierre" con un precio de ayer). Idempotente: upsert por (grupo, ric, fecha) —
re-correrlo pisa el mismo día con el último valor.

Cron: 21:10 UTC L-V (post cierre NY 20:00 UTC y rueda diurna CBOT ~18:20 UTC).
Uso:  python -m jobs.eikon_cierres
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, ".")

from core.eikon_bonos import filas_bonos_off
from core.eikon_chicago import tablero_chicago
from core.job_runs import JobRunLogger
from core.postgres import get_job_pool

_SQL_UPSERT = ("INSERT INTO mercado.eikon_cierres (grupo, ric, fecha, valor) "
               "VALUES (%s, %s, %s, %s) "
               "ON CONFLICT (grupo, ric, fecha) DO UPDATE SET valor = EXCLUDED.valor")


def _hoy_art():
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def run() -> dict:
    with JobRunLogger("eikon_cierres") as jr:
        hoy = _hoy_art()
        filas: list[tuple] = []

        # Soberanos offshore: valor = precio resuelto (fallback chain), solo
        # si el snapshot es de HOY. filas_bonos_off ya trae updated_at + ric
        # implícito en el mapeo — acá necesitamos el ric: lo reconstruimos.
        from core.eikon_bonos import BONOS_OFF
        bono_a_ric = {b: r for r, b in BONOS_OFF.items()}
        for b in filas_bonos_off():
            upd = b.get("updated_at")
            if b.get("precio") is None or upd is None:
                continue
            if (upd - timedelta(hours=3)).date() != hoy:
                continue   # snapshot viejo — el feed no corrió hoy
            filas.append(("bonos_off", bono_a_ric[b["bono"]], hoy, b["precio"]))

        # Chicago: valor = precio ya convertido a USD/t (el tablero lo resuelve
        # con el factor de la familia — fuente única).
        for fam in tablero_chicago().get("familias") or []:
            for r in fam.get("rows") or []:
                upd = r.get("updated_at")
                if r.get("precio") is None or upd is None:
                    continue
                if (upd - timedelta(hours=3)).date() != hoy:
                    continue
                filas.append(("chicago", r["ric"], hoy, r["precio"]))

        if filas:
            with get_job_pool().connection() as conn, conn.cursor() as cur:
                cur.executemany(_SQL_UPSERT, filas)
                conn.commit()

        n_bonos = sum(1 for f in filas if f[0] == "bonos_off")
        n_chi = sum(1 for f in filas if f[0] == "chicago")
        jr.set_stat("bonos_off", n_bonos)
        jr.set_stat("chicago", n_chi)
        jr.log(f"cierres {hoy}: {n_bonos} bonos off · {n_chi} chicago "
               f"({'feed corrió hoy' if filas else 'SIN datos de hoy — feed apagado'})")
        return {"fecha": hoy.isoformat(), "bonos_off": n_bonos, "chicago": n_chi}


def main() -> int:
    print(f"→ {run()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
