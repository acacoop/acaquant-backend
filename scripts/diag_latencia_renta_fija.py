"""Diag de latencia de la pantalla /renta-fija — read-only.

La página hace 9 fetches al backend en Promise.all (corren en paralelo) → la
espera del usuario = el endpoint MÁS LENTO, no la suma. Este diag cronometra
cada uno de los 9 en FRÍO (cache miss, primera llamada del proceso) y en
CALIENTE (segunda llamada, debería pegar al @cached), respetando los flags SQL
de prod. Así sabemos cuál query quedó cara tras la migración a SQL.

Uso (en el Droplet, raíz del repo):
    python -m scripts.diag_latencia_renta_fija

NO escribe nada. Solo mide y ordena por tiempo en frío.
"""
from __future__ import annotations

import os
import time

# Selectores idénticos a api/routers/cotizaciones.py (mismo flag, misma lógica).
from api.services import derivados as svc_der
from api.services import fair_value as svc_fv
from api.services import mercado_hist_sql as svc_mhist
from api.services import renta_fija as svc_rf
from api.services import renta_fija_sql as svc_rf_sql
from api.services.titulos_flujos import flujos_instrumentos

_RF_SQL = os.getenv("RENTA_FIJA_SQL") == "1"
_HIST_SQL = os.getenv("MERCADO_HIST_SQL") == "1"

_rf = svc_rf_sql if _RF_SQL else svc_rf          # get_renta_fija
_fwbe = svc_mhist if _RF_SQL else svc_der        # get_forwards / breakevens / zscore
_hist = svc_mhist if _HIST_SQL else svc_der      # get_historico_*


def _t(fn) -> tuple[float, str]:
    """Corre fn(), devuelve (ms, resumen del resultado o error)."""
    t0 = time.perf_counter()
    try:
        res = fn()
        ms = (time.perf_counter() - t0) * 1000
        n = len(res) if isinstance(res, (list, dict)) else "?"
        return ms, f"ok (n={n})"
    except Exception as e:  # diag: queremos ver cualquier fallo
        ms = (time.perf_counter() - t0) * 1000
        return ms, f"ERROR {type(e).__name__}: {e}"


# Los 9 fetches que dispara src/app/renta-fija/page.tsx, en orden.
CASOS: list[tuple[str, object]] = [
    ("renta-fija",              lambda: _rf.get_renta_fija()),
    ("forwards",                lambda: _fwbe.get_forwards()),
    ("titulos/flujos",          lambda: flujos_instrumentos()),
    ("breakevens",              lambda: _fwbe.get_breakevens()),
    ("historico/breakevens",    lambda: _hist.get_historico_breakevens()),
    ("historico/forwards",      lambda: _hist.get_historico_forwards()),
    ("forwards-zscore",         lambda: _fwbe.get_forwards_zscore()),
    ("fair-value(tasa_fija)",   lambda: svc_fv.get_fair_value_live(curva="tasa_fija")),
    ("fair-value(cer)",         lambda: svc_fv.get_fair_value_live(curva="cer")),
]


def main() -> None:
    print(f"Flags: RENTA_FIJA_SQL={'on' if _RF_SQL else 'off'}  "
          f"MERCADO_HIST_SQL={'on' if _HIST_SQL else 'off'}\n")
    print(f"{'endpoint':24} {'frío (ms)':>12} {'caliente (ms)':>14}  detalle")
    print("-" * 72)

    filas = []
    for nombre, fn in CASOS:
        frio_ms, detalle = _t(fn)        # cold: primera llamada del proceso
        cal_ms, _ = _t(fn)               # warm: debería pegar al @cached
        filas.append((nombre, frio_ms, cal_ms, detalle))

    # Ordenado por tiempo en frío desc — el de arriba es el cuello de botella
    # (con Promise.all, la espera del user ≈ este).
    for nombre, frio_ms, cal_ms, detalle in sorted(filas, key=lambda r: -r[1]):
        print(f"{nombre:24} {frio_ms:12.0f} {cal_ms:14.0f}  {detalle}")

    peor = max(filas, key=lambda r: r[1])
    print("-" * 72)
    print(f"\nCuello de botella en frío: {peor[0]} ({peor[1]:.0f} ms). "
          "La pantalla NO puede ir más rápido que esto en un cache miss.")


if __name__ == "__main__":
    main()
