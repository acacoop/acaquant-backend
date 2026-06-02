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
from core.mongo import get_mongo_client

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
                  liq_desde: str, liq_hasta: str) -> list[dict]:
    """Informes de una cuenta → filas crudas. Filtra por concertación (lo que
    importa); manda liquidación amplia porque el endpoint la exige. Silencioso
    ante error/204."""
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
            timeout=60,
        )
        if resp.status_code != 200:
            return []
        body = (resp.text or "").strip()
        data = resp.json() if body else []
        if not isinstance(data, list):
            return []
        return [_item_to_row(it) for it in data if isinstance(it, dict) and it.get("boleto")]
    except Exception:
        return []


def run(desde_d: date, hasta_d: date, workers: int) -> dict:
    with JobRunLogger("operaciones_informes") as jr:
        client = get_mongo_client()
        db = client["CashFlow"]
        coll = db["Operaciones"]
        maps = svc.cargar_maps_enrich(db)

        cuentas = sorted({
            str(c).strip()
            for c in client["Clientes"]["Comitentes"].distinct("id_cuenta")
            if c not in (None, "")
        })
        # Ventana de concertación (lo que filtramos) + liquidación amplia (requerida).
        conc_desde, conc_hasta = _ddmmyyyy(desde_d), _ddmmyyyy(hasta_d)
        liq_desde = _ddmmyyyy(desde_d)
        liq_hasta = _ddmmyyyy(hasta_d + timedelta(days=_BUFFER_LIQ))
        jr.log(f"Concertación {conc_desde}..{conc_hasta} | {len(cuentas)} cuentas | workers={workers}")

        rows: list[dict] = []
        con_ops = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_fetch_cuenta, c, conc_desde, conc_hasta, liq_desde, liq_hasta)
                    for c in cuentas]
            for fut in as_completed(futs):
                r = fut.result()
                if r:
                    con_ops += 1
                    rows.extend(r)

        res = svc.ingestar_filas(coll, rows, enrich_maps=maps)
        jr.set_stat("cuentas", len(cuentas))
        jr.set_stat("cuentas_con_ops", con_ops)
        jr.set_stat("filas", len(rows))
        jr.set_stat("upsertadas", res["upsertadas"])
        jr.set_stat("modificadas", res["modificadas"])
        jr.log(f"OK: {len(rows)} filas → {res['upsertadas']} nuevas / {res['modificadas']} act")
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
