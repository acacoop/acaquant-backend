"""jobs/acreencias.py — precompute diario del MOTOR DE ACREENCIAS.

Cruza las tenencias actuales (SQL portafolio.tenencia, aum='si') con el
calendario contractual de cada instrumento (Trading.Curvas.flujos) y persiste,
por cliente y fecha de pago, cuánto va a cobrar a futuro → SQL
`operaciones.acreencias` (swap atómico TRUNCATE+INSERT).

SQL-NATIVE (cutover 2026-06-23): la escritura Mongo `CashFlow.Acreencias` fue
ELIMINADA — la única escritura es a SQL vía `replace_native`. La colección Mongo
quedó lista para drop (la vista lee SQL).

Cron: 1x/día post-tenencia → ej. 23:45 UTC L-V.

  python -m jobs.acreencias              # DRY-RUN: resumen + muestra, no escribe
  python -m jobs.acreencias --commit     # escribe operaciones.acreencias (swap atómico)

Idempotente: re-correrlo reemplaza la tabla entera (swap sin ventana de vacío).
REGLA #2: el dry-run muestra los números antes de mostrarle plata a un cliente;
validá una muestra contra una valuación conocida antes de --commit.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from api.services.acreencias import computar_acreencias


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true",
                    help="escribe operaciones.acreencias (SQL)")
    args = ap.parse_args()

    # incluir_hoy=True: la proyección incluye los pagos que caen HOY (no solo > hoy)
    # para que el briefing de apertura pueda decir "el bono X paga hoy". Efecto: la
    # vista de back-office de acreencias muestra también el día en curso.
    docs = computar_acreencias(incluir_hoy=True)
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
        # SQL-NATIVE: única escritura es el swap atómico TRUNCATE+INSERT a
        # operaciones.acreencias (sin PK natural → surrogate IDENTITY). fecha_pago/
        # snapshot ya son ISO strings; `data` lleva el doc completo (generado_at aware
        # → ISO recursivo via doc_iso). La columna `id` IDENTITY se genera sola.
        from core.pg_mirror import doc_iso, replace_native
        rows = [{
            "fecha_pago": d["fecha_pago"], "id_cuenta": d.get("id_cuenta"),
            "ticker": d.get("ticker"), "moneda": d.get("moneda"),
            "monto": d.get("monto"), "data": doc_iso(d),
        } for d in docs]
        n_sql = replace_native("operaciones.acreencias", rows)
        jr.set_stat("acreencias", n_sql)
    print(f"\n✅ {n_sql} acreencias escritas a operaciones.acreencias (SQL, swap atómico).")


if __name__ == "__main__":
    main()
