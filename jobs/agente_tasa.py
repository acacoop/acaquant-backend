"""`jobs/agente_tasa.py` — la LISTA DE PRIORIDAD contra 1816, cada 15 min.

Le pide a 1816 la TEA, la duration y el precio de los bonos que operan y a los
que el motor no les calcula la tasa (los que encuentra `bono_sin_tasa`).

**Patrón TAMAR**: NO escribe en `mercado.market_snapshot` —esa tabla es del
motor, live, cada 5 s— sino en la suya. Se juntan en la LECTURA y cada fila
viaja diciendo de dónde salió su tasa.

    python -m jobs.agente_tasa
    python -m jobs.agente_tasa --ver      # qué tickers hay en la lista
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("agente_tasa")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ver", action="store_true")
    a = ap.parse_args()

    from agente import tasa_1816
    from core.job_runs import JobRunLogger

    if a.ver:
        tk = tasa_1816.pendientes()
        print(f"{len(tk)} en la lista: {', '.join(tk[:40])}")
        return 0

    with JobRunLogger("agente_tasa") as jr:
        purgadas = tasa_1816.purgar()
        jr.set_stat("purgadas", purgadas)
        r = tasa_1816.refrescar()
        jr.set_stat("tickers", r.get("tickers", 0))
        jr.set_stat("escritos", r.get("escritos", 0))
        if not r.get("ok"):
            jr.error(str(r.get("error"))[:300])
        print(f"lista: {r.get('tickers', 0)} · escritos: {r.get('escritos', 0)}"
              f" · purgadas: {purgadas}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
