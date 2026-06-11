"""jobs/acreencias.py — precompute diario del MOTOR DE ACREENCIAS.

Cruza las tenencias actuales (Valuaciones.AuM último snapshot) con el calendario
contractual de cada instrumento (Trading.Curvas.flujos) y persiste, por cliente
y fecha de pago, cuánto va a cobrar a futuro → `CashFlow.Acreencias`.

Cron: 1x/día post-AuM (jobs.aum corre 23 UTC L-V) → ej. 23:30 UTC L-V.

  python -m jobs.acreencias              # DRY-RUN: resumen + muestra, no escribe
  python -m jobs.acreencias --commit     # escribe CashFlow.Acreencias (swap atómico)

Idempotente: re-correrlo reemplaza la colección entera (swap sin ventana de
vacío). REGLA #2: el dry-run muestra los números antes de mostrarle plata a un
cliente; validá una muestra contra una valuación conocida antes de --commit.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from api.services.acreencias import computar_acreencias
from core.mongo import get_mongo_client, reemplazar_coleccion_atomico


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="escribe CashFlow.Acreencias")
    args = ap.parse_args()

    docs = computar_acreencias()
    print(f"{len(docs)} acreencias proyectadas (cliente·fecha·ticker)")
    if not docs:
        print("Sin acreencias: ¿hay AuM con snapshot y instrumentos en Curvas con flujos futuros?")
        return

    por_moneda: dict[str, float] = defaultdict(float)
    fechas, clientes, tickers = set(), set(), set()
    for d in docs:
        por_moneda[d["moneda"]] += d["monto"]
        fechas.add(d["fecha_pago"])
        clientes.add(d["id_cuenta"])
        tickers.add(d["ticker"])
    print(f"Total a cobrar por moneda: {dict((m, round(v, 2)) for m, v in por_moneda.items())}")
    print(f"{len(clientes)} clientes · {len(tickers)} instrumentos · {len(fechas)} fechas "
          f"· rango {min(fechas)} → {max(fechas)}")

    print("\nPróximos pagos (muestra, ordenado por fecha):")
    print(f"  {'fecha':<12}{'cliente':<26}{'ticker':<9}{'mon':<4}{'monto':>16}")
    for d in sorted(docs, key=lambda x: (x["fecha_pago"], -x["monto"]))[:20]:
        print(f"  {d['fecha_pago']:<12}{(d['cliente'] or '?')[:24]:<26}"
              f"{d['ticker']:<9}{d['moneda']:<4}{d['monto']:>16,.2f}")

    if not args.commit:
        print("\n(DRY-RUN — no se escribió nada. Validá una muestra y corré con --commit.)")
        return

    from core.job_runs import JobRunLogger
    with JobRunLogger("acreencias") as jr:
        db = get_mongo_client()["CashFlow"]
        n = reemplazar_coleccion_atomico(db, "Acreencias", docs)
        db["Acreencias"].create_index("fecha_pago")
        db["Acreencias"].create_index("id_cuenta")
        jr.set_stat("acreencias", n)
    print(f"\n✅ {n} acreencias escritas a CashFlow.Acreencias (swap atómico + índices).")


if __name__ == "__main__":
    main()
