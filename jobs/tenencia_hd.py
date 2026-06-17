"""jobs/tenencia_hd.py — materializa la Tenencia Valorizada (cartera HD) de las
cuentas propias 100/255/256, 1 doc por día.

Por qué una colección y no leer AuM en vivo (ver docs/ARQUITECTURA.md):
  * FREEZE: el valor de cada día se congela al capturarlo. Si el AuM se corrige
    retroactivamente, el reporte histórico NO cambia solo.
  * Las cuentas propias (100/255) se filtran de la VISTA AuM → camino dedicado.
  * Lectura O(1) en el endpoint (patrón rollup-no-escanear, REGLA #4).

Escribe `Valuaciones.TenenciaHD`, grano 1 doc por `fecha_snapshot`:
    { fecha_snapshot, tc, aum:{ "100":…, "255":…, "256":… }, total,
      posiciones:[ {unidad, "100":val, "255":val, "256":val, total,     # valuación (dinero)
                    precio, cant:{ "100":…, "255":…, "256":… }, total_cant} … ],
      generado_en }
El switch DINERO/NOMINAL del front usa `cant`; el editor manual corrige `precio`
y recalcula la valuación (cantidad × precio / 100, HD = paridad).

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
from core.postgres import get_job_pool

CUENTAS = ["100", "255", "256"]
_CARTERA = "HD"
_THROTTLE = 0.1   # pausa entre días en el backfill (no starvar la DB)


def _hd_unidades() -> list[str]:
    """unidades con CARTERA=HD desde SQL portafolio.assets (fuente de verdad)."""
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT unidad FROM portafolio.assets WHERE cartera = %s "
                    "AND unidad IS NOT NULL", (_CARTERA,))
        return sorted({r[0] for r in cur.fetchall()})


def _doc_del_dia(fecha: str, hd_unidades: list[str], now: datetime) -> dict:
    """Arma el doc de tenencia HD para una fecha: AuM por cuenta + posiciones por
    título + el TC (MEP) de ESE día congelado (para dolarizar reproducible).
    Lee SQL portafolio.tenencia (aum='si')."""
    from api.services._mep import get_mep_for_date
    por_unidad: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    cant_unidad: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    precio_unidad: dict[str, float] = {}
    aum: dict[str, float] = defaultdict(float)
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT unidad, id_cuenta, SUM(valuacion), SUM(cantidad), MAX(precio) "
            "FROM portafolio.tenencia "
            "WHERE fecha = %s AND aum = 'si' AND id_cuenta = ANY(%s) AND unidad = ANY(%s) "
            "GROUP BY unidad, id_cuenta", (fecha, CUENTAS, hd_unidades))
        for u, c, v, cant, prec in cur.fetchall():
            c = str(c)
            v = float(v or 0.0)
            por_unidad[u][c] += v
            cant_unidad[u][c] += float(cant or 0.0)
            if prec is not None:
                precio_unidad[u] = float(prec)
            aum[c] += v

    posiciones = []
    for u, byc in sorted(por_unidad.items(), key=lambda kv: -sum(kv[1].values())):
        cantc = cant_unidad[u]
        # `precio` + `cant` (cantidad nominal por cuenta) NUEVOS — habilitan el switch
        # DINERO/NOMINAL y el editor manual de precio. Las claves 100/255/256 siguen
        # siendo la VALUACIÓN (compat con el path viejo).
        fila = {
            "unidad":     u,
            "total":      round(sum(byc.values()), 2),
            "precio":     round(precio_unidad[u], 4) if u in precio_unidad else None,
            "cant":       {c: round(cantc.get(c, 0.0), 4) for c in CUENTAS},
            "total_cant": round(sum(cantc.values()), 4),
        }
        fila.update({c: round(byc.get(c, 0.0), 2) for c in CUENTAS})
        posiciones.append(fila)

    tc = get_mep_for_date(fecha)   # MEP de ese día (ARS/USD); None si no hay feed
    return {
        "fecha_snapshot": fecha,
        "tc": round(tc, 2) if tc else None,
        "aum": {c: round(aum.get(c, 0.0), 2) for c in CUENTAS},
        "total": round(sum(aum.values()), 2),
        "posiciones": posiciones,
        "generado_en": now,
    }


def run(fecha: str | None = None, backfill: bool = False, desde: str | None = None) -> dict:
    with JobRunLogger("tenencia_hd") as jr:
        client = get_mongo_client()
        ten_col = client["Valuaciones"]["TenenciaHD"]
        ten_col.create_index("fecha_snapshot", unique=True)   # idempotente
        now = datetime.now(UTC)

        hd = _hd_unidades()
        if not hd:
            raise RuntimeError(f"No hay unidades con CARTERA={_CARTERA} en Assets — abortando.")

        # Qué fechas procesar (desde SQL portafolio.tenencia, aum='si').
        if backfill:
            if not desde:
                raise ValueError("--backfill requiere --desde YYYY-MM-DD")
            with get_job_pool().connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT DISTINCT fecha FROM portafolio.tenencia "
                            "WHERE aum = 'si' AND id_cuenta = ANY(%s) AND fecha >= %s "
                            "ORDER BY fecha", (CUENTAS, desde))
                fechas = [r[0].isoformat() for r in cur.fetchall()]
        elif fecha:
            fechas = [fecha]
        else:
            with get_job_pool().connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
                m = cur.fetchone()[0]
            fechas = [m.isoformat()] if m else []

        n = 0
        for f in fechas:
            doc = _doc_del_dia(f, hd, now)
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
