"""operaciones_informes.py — ingesta de operaciones desde Aunesa /informes a
CashFlow.Operaciones (fuente de verdad de operaciones, por CONCERTACIÓN).

Cada 30 min (10:30-19:00 ART, L-V): consulta /informes por cuenta EN PARALELO
filtrando por Fecha CONCERTACIÓN (`fechaConcDesde`/`fechaConcHasta`, igual que
flujo_contrapartes), normaliza y upsertea por boleto (idempotente, sin duplicar
gracias al índice único) + enriquece inline (moneda/mercado/operacion/segmento)
sin scan completo.

Uso:
    python -m jobs.operaciones_informes
    python -m jobs.operaciones_informes --desde 2026-06-01 --hasta 2026-06-01
    python -m jobs.operaciones_informes --workers 12
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta

sys.path.insert(0, ".")

from api.services import operaciones_informes as svc
from api.services.aunesa_informes import parse_monto
from core import aunesa
from core.job_runs import JobRunLogger
from core.postgres import get_job_pool

_INFORMES = "operaciones/informes"
# Filtramos por CONCERTACIÓN (lo que interesa). Pero el endpoint EXIGE
# fechaDesde/fechaHasta (LIQUIDACIÓN) al consultar por cuenta → mandamos una
# ventana de liquidación amplia (concertación + buffer hacia adelante para
# cubrir T+N) y dejamos que fechaConc filtre lo exacto.
_LOOKBACK_CONC = 2     # días de concertación hacia atrás (correcciones)
_BUFFER_LIQ = 10       # días extra de liquidación hacia adelante (T+N)
_WORKERS = 10


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _arancel(item: dict) -> float:
    tot = 0.0
    for row in item.get("aranceles") or []:
        if isinstance(row, dict):
            tot += parse_monto(row.get("Monto"))
    return round(tot, 2)


def _item_to_row(it: dict) -> dict:
    return {
        "boleto":         it.get("boleto"),
        "concertacion":   it.get("concertacion"),
        "cuenta":         it.get("cuenta"),
        "denominacion":   it.get("denominacion"),
        "tipo_operacion": it.get("tipoOperacion"),
        "instrumento":    it.get("instrumento"),
        "condiciones":    it.get("condiciones"),
        "cantidad":       it.get("cantidadTotal"),
        "bruto":          it.get("bruto"),
        "arancel":        _arancel(it),
    }


def _fetch_cuenta(cuenta: str, conc_desde: str, conc_hasta: str,
                  liq_desde: str, liq_hasta: str) -> tuple[list[dict], bool]:
    """Informes de una cuenta → (filas, ok). ok=False si hubo error/timeout
    (para observabilidad: contamos cuántas cuentas fallaron). 204 = ok sin ops."""
    try:
        resp = aunesa.get(
            _INFORMES,
            {
                "cuenta":         str(cuenta),
                "fechaDesde":     liq_desde,   # liquidación (requeridas por el endpoint)
                "fechaHasta":     liq_hasta,
                "fechaConcDesde": conc_desde,  # concertación (el filtro que nos interesa)
                "fechaConcHasta": conc_hasta,
            },
            timeout=90,
        )
        if resp.status_code == 204:
            return [], True   # sin operaciones — OK
        if resp.status_code != 200:
            return [], False  # error de la API
        body = (resp.text or "").strip()
        data = resp.json() if body else []
        if not isinstance(data, list):
            return [], False
        return [_item_to_row(it) for it in data if isinstance(it, dict) and it.get("boleto")], True
    except Exception:
        return [], False  # timeout u otra falla


def run(desde_d: date, hasta_d: date, workers: int) -> dict:
    with JobRunLogger("operaciones_informes") as jr:
        # Catálogo TiposOperacion SQL-native (operaciones.tipos_operacion) — ver
        # operaciones_informes.cargar_maps_enrich (decomiso Mongo).
        maps = svc.cargar_maps_enrich()

        # Fuente de cuentas: TODAS las que ya operan (SQL operaciones) + comitentes.
        # Comitentes solo NO alcanza: los FCI/sociedades gerentes y la cuenta
        # propia de la empresa no son comitentes y se perdían.
        with get_job_pool().connection() as _cn, _cn.cursor() as _cu:
            _cu.execute("SELECT id_cuenta FROM comitentes WHERE id_cuenta IS NOT NULL")
            cuentas_comit = {str(r[0]).strip() for r in _cu.fetchall() if r[0] not in (None, "")}
            _cu.execute("SELECT DISTINCT id_cuenta FROM operaciones WHERE id_cuenta IS NOT NULL")
            cuentas_ops = {str(r[0]).strip() for r in _cu.fetchall() if r[0] not in (None, "")}
            # clientes.cuentas incluye además las cuentas PROPIA (mesa propia de la
            # empresa), que NO son comitentes. Sin esto quedaban afuera del universo:
            # no estaban en comitentes (sync trae solo Comitente) ni en operaciones
            # (0 boletos) → chicken-and-egg, no podían bootstrappear nunca.
            _cu.execute("SELECT id_cuenta FROM cuentas WHERE id_cuenta IS NOT NULL")
            cuentas_maestro = {str(r[0]).strip() for r in _cu.fetchall() if r[0] not in (None, "")}
        cuentas = sorted(cuentas_comit | cuentas_ops | cuentas_maestro)
        # Ventana de concertación (lo que filtramos) + liquidación amplia (requerida).
        conc_desde, conc_hasta = _ddmmyyyy(desde_d), _ddmmyyyy(hasta_d)
        liq_desde = _ddmmyyyy(desde_d)
        liq_hasta = _ddmmyyyy(hasta_d + timedelta(days=_BUFFER_LIQ))
        jr.log(f"Concertación {conc_desde}..{conc_hasta} | {len(cuentas)} cuentas "
               f"({len(cuentas_ops)} de Operaciones + {len(cuentas_comit)} comitentes) | workers={workers}")

        rows: list[dict] = []
        con_ops = 0
        fallidas: list[str] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_fetch_cuenta, c, conc_desde, conc_hasta, liq_desde, liq_hasta): c
                    for c in cuentas}
            for fut in as_completed(futs):
                rows_c, ok = fut.result()
                if not ok:
                    fallidas.append(futs[fut])
                if rows_c:
                    con_ops += 1
                    rows.extend(rows_c)

        res = svc.ingestar_filas_sql(rows, enrich_maps=maps)
        jr.set_stat("cuentas", len(cuentas))
        jr.set_stat("cuentas_con_ops", con_ops)
        jr.set_stat("cuentas_fallidas", len(fallidas))
        jr.set_stat("filas", len(rows))
        jr.set_stat("upsertadas", res["upsertadas"])
        jr.set_stat("modificadas", res["modificadas"])
        if fallidas:
            jr.log(f"⚠ {len(fallidas)} cuentas fallaron (timeout/error). Ej: {fallidas[:15]}")
        jr.log(f"OK: {len(rows)} filas → {res['upsertadas']} nuevas / {res['modificadas']} act "
               f"· {con_ops} cuentas con ops · {len(fallidas)} fallidas")
        return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", help="YYYY-MM-DD concertación desde (default hoy-2 ART)")
    ap.add_argument("--hasta", help="YYYY-MM-DD concertación hasta (default hoy ART)")
    ap.add_argument("--workers", type=int, default=_WORKERS)
    args = ap.parse_args()
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()  # ART
    desde_d = (datetime.strptime(args.desde, "%Y-%m-%d").date() if args.desde
               else hoy - timedelta(days=_LOOKBACK_CONC))
    hasta_d = (datetime.strptime(args.hasta, "%Y-%m-%d").date() if args.hasta
               else hoy)
    res = run(desde_d, hasta_d, args.workers)
    print(f"→ {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
