"""jobs/portafolio_reparar_timeouts.py — recupera los TIMEOUT/ERROR del backfill.

Lee `portafolio.backfill_log`, toma las (fecha, cuenta) que quedaron en `timeout`
o `error_*` y las re-consulta a Aunesa UNA POR UNA (secuencial, timeout amplio),
con la MISMA regla de fecha corregida (desde = fecha + 1 hábil). Escribe cada una
en `portafolio.tenencia` y actualiza el log a ok/vacía.

Pensado para correr EN PARALELO al backfill principal: el log de cada fecha se
escribe recién cuando esa fecha TERMINA, así que el reparador solo ve fechas ya
cerradas (las que el backfill aún procesa todavía no aparecen) → no chocan, nunca
escriben la misma (fecha, cuenta) a la vez.

Idempotente y repetible: re-correr solo reintenta lo que siga en timeout/error.

Uso:
    python -m jobs.portafolio_reparar_timeouts            # una pasada
    python -m jobs.portafolio_reparar_timeouts --loop     # repite hasta que no queden (o no haya progreso)
    python -m jobs.portafolio_reparar_timeouts --timeout 300
"""
from __future__ import annotations

import sys
import time
from datetime import date

import requests

from core.postgres import get_job_pool
from jobs.aum import _SESSION, POSICION_URL, autenticar
from jobs.portafolio_backfill import (
    _PARAMS_BASE,
    _load_assets_map,
    _parse,
    _prox_habil,
    _write_date,
    cargar_contrapartes,
)

TIMEOUT = 300
RETRIES = 3


def _opt(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _pendientes() -> list[tuple[str, str, str]]:
    """(fecha_iso, id_cuenta, status) de todo lo que NO quedó ok/vacía."""
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT fecha::text, id_cuenta, status FROM portafolio.backfill_log "
            "WHERE status NOT IN ('ok', 'vacia') ORDER BY fecha, id_cuenta")
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def _fetch_one(hdr: dict, idc: str, desde: str, fecha_iso: str, amap: dict, timeout: int) -> tuple:
    """Consulta individual con timeout amplio. Devuelve (status, registros)."""
    params = {"desde": desde, **_PARAMS_BASE}
    for intento in range(1, RETRIES + 1):
        try:
            resp = _SESSION.get(POSICION_URL.format(idc), params=params,
                                headers=hdr["h"], timeout=timeout)
            if resp.status_code == 401:
                hdr["h"] = autenticar()
                continue
            if resp.status_code == 204:
                return "vacia", []
            if resp.status_code != 200:
                if intento < RETRIES:
                    time.sleep(2 ** intento)
                    continue
                return f"error_http_{resp.status_code}", []
            return "ok", _parse(resp.json(), idc, "", fecha_iso, amap)
        except requests.exceptions.Timeout:
            if intento < RETRIES:
                continue
            return "timeout", []
        except Exception as e:
            if intento < RETRIES:
                time.sleep(2 ** intento)
                continue
            return f"error:{type(e).__name__}", []
    return "error_reauth", []


def main() -> int:
    loop = "--loop" in sys.argv
    timeout = int(_opt("--timeout", TIMEOUT))

    cargar_contrapartes()   # para que _parse marque `aum` bien (regla _aum_filters)
    amap = _load_assets_map()
    hdr = {"h": autenticar()}
    pasada = 0
    while True:
        pasada += 1
        pend = _pendientes()
        if not pend:
            print("✅ No quedan timeouts/errores pendientes en el log.")
            return 0
        print(f"\n[pasada {pasada}] pendientes a reparar: {len(pend)}")
        recuperadas = 0
        for i, (fecha_iso, idc, _st) in enumerate(pend, 1):
            desde = _prox_habil(date.fromisoformat(fecha_iso)).strftime("%d/%m/%Y")
            status, recs = _fetch_one(hdr, idc, desde, fecha_iso, amap, timeout)
            st_log = status if status in ("ok", "vacia", "timeout") else status[:40]
            _write_date(fecha_iso, recs, {idc: (st_log, len(recs), "reparado")})
            if status in ("ok", "vacia"):
                recuperadas += 1
            mark = "✓" if status in ("ok", "vacia") else "✗"
            print(f"  [{i}/{len(pend)}] {mark} {fecha_iso} {idc:<7} {status:<14} filas={len(recs)}")
        print(f"[pasada {pasada}] recuperadas {recuperadas}/{len(pend)}")
        if not loop:
            return 0
        if recuperadas == 0:
            print("Sin progreso en esta pasada (siguen dando timeout) — corto.")
            return 0
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
