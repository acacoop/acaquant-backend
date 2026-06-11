"""jobs/tenencia_hd.py — materializa la Tenencia Valorizada (cartera HD) de las
cuentas propias 100/255/256, 1 doc por día.

Por qué una colección y no leer AuM en vivo (ver docs/ARQUITECTURA.md):
  * FREEZE: el valor de cada día se congela al capturarlo. Si el AuM se corrige
    retroactivamente, el reporte histórico NO cambia solo.
  * Las cuentas propias (100/255) se filtran de la VISTA AuM → camino dedicado.
  * Lectura O(1) en el endpoint (patrón rollup-no-escanear, REGLA #4).

Escribe `Valuaciones.TenenciaHD`, grano 1 doc por `fecha_snapshot`:
    { fecha_snapshot, aum:{ "100":…, "255":…, "256":… }, total,
      posiciones:[ {unidad, "100":…, "255":…, "256":…, total} … ], generado_en }

Solo títulos con CARTERA=HD (resuelto desde Valuaciones.Assets en cada corrida).

Modos:
    python -m jobs.tenencia_hd                         # día: último snapshot de AuM
    python -m jobs.tenencia_hd --fecha 2026-06-10      # un día puntual
    python -m jobs.tenencia_hd --backfill --desde 2026-04-01   # rango (días que existen en AuM)

Idempotente: UPSERT por fecha_snapshot. Re-correrlo recaptura (si una cuenta llegó
tarde al AuM, el re-run la incluye). Cron: 1×/día hábil después de jobs.aum.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime

sys.path.insert(0, ".")

from pymongo import UpdateOne

from core.job_runs import JobRunLogger
from core.mongo import get_mongo_client

CUENTAS = ["100", "255", "256"]
_CARTERA = "HD"
_THROTTLE = 0.1   # pausa entre días en el backfill (no starvar la DB)


def _hd_unidades(client) -> list[str]:
    """unidades con CARTERA=HD desde Valuaciones.Assets (fuente de verdad)."""
    return sorted({
        d["unidad"] for d in client["Valuaciones"]["Assets"].find(
            {"CARTERA": _CARTERA}, {"_id": 0, "unidad": 1}) if d.get("unidad")
    })


def _doc_del_dia(aum_col, fecha: str, hd_unidades: list[str], now: datetime) -> dict:
    """Arma el doc de tenencia HD para una fecha: AuM por cuenta + posiciones por título."""
    por_unidad: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    aum: dict[str, float] = defaultdict(float)
    for r in aum_col.aggregate([
        {"$match": {"fecha_snapshot": fecha, "id_cuenta": {"$in": CUENTAS},
                    "unidad": {"$in": hd_unidades}}},
        {"$group": {"_id": {"u": "$unidad", "c": "$id_cuenta"}, "v": {"$sum": "$valuacion"}}},
    ]):
        u = r["_id"]["u"]
        c = str(r["_id"]["c"])
        v = float(r.get("v") or 0.0)
        por_unidad[u][c] += v
        aum[c] += v

    posiciones = []
    for u, byc in sorted(por_unidad.items(), key=lambda kv: -sum(kv[1].values())):
        fila = {"unidad": u, "total": round(sum(byc.values()), 2)}
        fila.update({c: round(byc.get(c, 0.0), 2) for c in CUENTAS})
        posiciones.append(fila)

    return {
        "fecha_snapshot": fecha,
        "aum": {c: round(aum.get(c, 0.0), 2) for c in CUENTAS},
        "total": round(sum(aum.values()), 2),
        "posiciones": posiciones,
        "generado_en": now,
    }


def run(fecha: str | None = None, backfill: bool = False, desde: str | None = None) -> dict:
    with JobRunLogger("tenencia_hd") as jr:
        client = get_mongo_client()
        aum_col = client["Valuaciones"]["AuM"]
        ten_col = client["Valuaciones"]["TenenciaHD"]
        ten_col.create_index("fecha_snapshot", unique=True)   # idempotente
        now = datetime.now(UTC)

        hd = _hd_unidades(client)
        if not hd:
            raise RuntimeError(f"No hay unidades con CARTERA={_CARTERA} en Assets — abortando.")

        # Qué fechas procesar.
        if backfill:
            if not desde:
                raise ValueError("--backfill requiere --desde YYYY-MM-DD")
            fechas = sorted(
                str(f) for f in aum_col.distinct(
                    "fecha_snapshot",
                    {"id_cuenta": {"$in": CUENTAS}, "fecha_snapshot": {"$gte": desde}})
                if f)
        elif fecha:
            fechas = [fecha]
        else:
            snap = aum_col.find_one({}, {"_id": 0, "fecha_snapshot": 1},
                                    sort=[("fecha_snapshot", -1)])
            fechas = [snap["fecha_snapshot"]] if snap else []

        n = 0
        for f in fechas:
            doc = _doc_del_dia(aum_col, f, hd, now)
            ten_col.bulk_write([UpdateOne({"fecha_snapshot": f}, {"$set": doc}, upsert=True)])
            n += 1
            if backfill:
                time.sleep(_THROTTLE)

        jr.set_stat("dias", n)
        jr.set_stat("hd_unidades", len(hd))
        jr.log(f"Tenencia HD: {n} día(s) · {len(hd)} unidades HD · "
               f"{'backfill desde ' + desde if backfill else (fechas[0] if fechas else '—')}")
        return {"dias": n, "hd_unidades": len(hd)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha", help="un día puntual YYYY-MM-DD")
    ap.add_argument("--backfill", action="store_true", help="procesar un rango (con --desde)")
    ap.add_argument("--desde", help="inicio del backfill YYYY-MM-DD")
    args = ap.parse_args()
    res = run(fecha=args.fecha, backfill=args.backfill, desde=args.desde)
    print(f"→ {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
