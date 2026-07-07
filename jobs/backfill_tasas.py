"""backfill_tasas.py — recalcula TEA/TEM/duration/paridad de TODOS los bonos de
RENTA FIJA (mercado.curvas) y las persiste en mercado.market_snapshot, ON DEMAND.

Por qué existe: el motor (engines.curvas) solo recalcula un bono cuando cambia su
last_price o cuando recicla CER/MEP/A3500 (cache `ultimo_calculado`). Si un bono
quedó sin TEA (dato recién corregido, feed que volvió, motor que no estaba corriendo
cuando se movió el precio), su "--" no se llena hasta el próximo trade. Este job
fuerza el recálculo YA, sin esperar un tick.

Es el MISMO cálculo del motor (reusa engines.curvas.calcular_campos) corrido una vez,
sin el skip de cache. Escribe SOLO las columnas analíticas que logra calcular (igual
que el motor: si un feed está caído, NO pisa con NULL → no destruye datos buenos).

Uso (en el Droplet o desde el botón Manager → Validaciones):
  python -m jobs.backfill_tasas
"""
from __future__ import annotations

from datetime import datetime

from core import market_snapshot
from core.job_runs import JobRunLogger
from core.postgres import use_job_pool


def _recalcular() -> dict:
    from core.pg_mirror import write_snapshot
    from engines.curvas import (
        _CAMPOS_ANALITICOS,
        calcular_campos,
        cargar_a3500_actual,
        cargar_cer,
        cargar_dias_habiles,
        cargar_indexado_por_ticker,
        cargar_mep_actual,
    )

    curvas = cargar_indexado_por_ticker()
    cer = cargar_cer()
    dias = cargar_dias_habiles()
    mep = cargar_mep_actual()
    a3500 = cargar_a3500_actual()

    tickers = list(curvas.keys())
    lp = {r["ticker"]: r for r in market_snapshot.last_prices(tickers)}
    tea_antes = market_snapshot.metric_map(tickers, "tea")  # {ticker: tea} solo no-NULL

    rows: list[dict] = []
    rellenados: list[str] = []   # no tenían TEA y ahora sí
    refrescados = 0              # ya tenían TEA, se recalculó
    sin_precio = 0
    sin_tea = 0                  # con precio pero el motor no da TEA (queda como estaba)

    for ticker, instrumento in curvas.items():
        row_lp = lp.get(ticker)
        if not row_lp:
            sin_precio += 1
            continue

        fake = {"ticker": ticker, "price": row_lp["last_price"],
                "timestamp": row_lp.get("updated_at") or datetime.utcnow()}
        campos = calcular_campos(fake, instrumento, cer, dias, mep, a3500) or {}

        # Solo columnas presentes (paridad con el motor: no pisa con NULL lo que no calcula).
        row = {"ticker": ticker}
        row.update({f.lower(): campos[f] for f in _CAMPOS_ANALITICOS if f in campos})
        if len(row) > 1:
            rows.append(row)

        tenia = ticker in tea_antes
        tiene = campos.get("TEA") is not None
        if tiene and not tenia:
            rellenados.append(instrumento.get("ticker_corto") or ticker)
        elif tiene and tenia:
            refrescados += 1
        elif not tiene:
            sin_tea += 1

    escritos = write_snapshot("market_snapshot", ["ticker"], rows) if rows else 0

    return {
        "instrumentos": len(curvas),
        "escritos": escritos,
        "rellenados": sorted(rellenados),
        "refrescados": refrescados,
        "sin_precio": sin_precio,
        "sin_tea": sin_tea,
        "mep": mep,
        "a3500": a3500,
    }


def main() -> None:
    with JobRunLogger("backfill_tasas") as jr, use_job_pool():
        jr.log("recalculando TEA/TEM de mercado.curvas → market_snapshot…")
        r = _recalcular()

        if not r["a3500"]:
            jr.error("A3500 (feed MAE) OFFLINE — los bonos dolar_linked no se recalcularon "
                     "(se preservó lo que había). Correr con el feed arriba.")

        jr.set_stat("instrumentos", r["instrumentos"])
        jr.set_stat("escritos", r["escritos"])
        jr.set_stat("rellenados", len(r["rellenados"]))
        jr.set_stat("refrescados", r["refrescados"])
        jr.set_stat("sin_tea", r["sin_tea"])
        jr.set_stat("sin_precio", r["sin_precio"])

        det = f"({', '.join(r['rellenados'])})" if r["rellenados"] else ""
        jr.log(f"✅ {r['escritos']} filas escritas · {len(r['rellenados'])} bonos NUEVOS con TEA "
               f"{det} · {r['refrescados']} refrescados · {r['sin_tea']} sin TEA · "
               f"{r['sin_precio']} sin precio. MEP={r['mep']} A3500={r['a3500']}")


if __name__ == "__main__":
    main()
