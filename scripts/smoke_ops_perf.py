"""scripts/smoke_ops_perf.py — mide latencia Mongo vs SQL en la vista OPERACIONES.

100% LECTURA. Corre cada endpoint /ops/* por los dos motores (vía `?_engine`, salteando
el cache con `.__wrapped__`) y reporta el mejor tiempo de N corridas + el speedup. Mix
HONESTO: incluye casos donde Mongo usa el rollup pre-agregado (donde SQL empata) y casos
de scan histórico (donde SQL gana fuerte). Sirve para decidir/justificar el cutover.

    python -m scripts.smoke_ops_perf
"""
from __future__ import annotations

import time
from datetime import date, timedelta

from api.routers import operaciones as M

_N = 3  # corridas por motor (se reporta el mejor: descarta ruido)


def _ms(fn, **kw) -> float:
    fn(**kw)  # warmup (abre pool / conexión, no se mide)
    best = float("inf")
    for _ in range(_N):
        t = time.perf_counter()
        fn(**kw)
        best = min(best, (time.perf_counter() - t) * 1000)
    return best


def _row(label: str, handler, **kw) -> None:
    m = _ms(handler.__wrapped__, _engine="mongo", **kw)
    s = _ms(handler.__wrapped__, _engine="sql", **kw)
    flag = "🟢" if s < m else "🟡"
    print(f"  {label:38} mongo={m:8.1f}ms  sql={s:8.1f}ms  {m / s:5.1f}x {flag}")


def main() -> int:
    f0 = M.ops_fechas.__wrapped__(_engine="sql")["fechas"][0]["fecha"]
    f0d = date.fromisoformat(f0)
    mes0 = f"{f0[:7]}-01"
    ano0 = (f0d - timedelta(days=365)).isoformat()
    # denominacion real (fuerza el path live en Mongo).
    den0 = M.ops_resumen.__wrapped__(
        _engine="sql", moneda="ARS", desde=ano0, hasta=f0,
    )["por_denominacion"][0]["denominacion"]

    print(f"Latencia Mongo vs SQL — vista OPERACIONES (mejor de {_N}, sin cache)\n")
    _row("serie ARS (default: Mongo usa rollup)", M.ops_serie,
         moneda="ARS", mercado=None, operacion=None, denominacion=None,
         cuenta=None, segmento=None, scope=None)
    _row("serie ARS x denominacion (Mongo live)", M.ops_serie,
         moneda="ARS", mercado=None, operacion=None, denominacion=den0,
         cuenta=None, segmento=None, scope=None)
    _row("resumen 1 año", M.ops_resumen,
         moneda="ARS", mercado=None, desde=ano0, hasta=f0, operacion=None,
         denominacion=None, cuenta=None, segmento=None, scope=None)
    _row("aranceles 1 año (tablas live)", M.ops_aranceles,
         moneda="ARS", desde=ano0, hasta=f0, agg="MENSUAL", cuenta=None,
         instrumento=None, sel_dim=None, segmento=None, dim="nivel3",
         serie_full=False, scope=None)
    _row("aranceles serie_full (scan histórico)", M.ops_aranceles,
         moneda="ARS", desde=ano0, hasta=f0, agg="MENSUAL", cuenta=None,
         instrumento=None, sel_dim=None, segmento=None, dim="nivel3",
         serie_full=True, scope=None)
    _row("aranceles dim=operador (join)", M.ops_aranceles,
         moneda="ARS", desde=ano0, hasta=f0, agg="MENSUAL", cuenta=None,
         instrumento=None, sel_dim=None, segmento=None, dim="operador",
         serie_full=False, scope=None)
    _row("agro 1 año (nuestro_mensual histórico)", M.ops_agro,
         desde=ano0, hasta=f0, agg="MENSUAL", commodity=None, cuenta=None, scope=None)
    _row("boletos 1 mes", M.ops_boletos,
         desde=mes0, hasta=f0, moneda="ARS", denominacion=None, cuenta=None,
         operacion=None, mercado=None, segmento=None, scope=None)

    print("\n🟢 = SQL más rápido · 🟡 = Mongo igual o más rápido (suele ser el caso rollup).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
