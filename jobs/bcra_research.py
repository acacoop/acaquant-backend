"""jobs/bcra_research.py — sincroniza las series del BCRA a Postgres (tab BCRA).

Doc vivo: docs/RESEARCH_BCRA.md. Feed SEPARADO de jobs/bcra.py (motores) — no se
tocan. Alimenta `research.bcra_series` SOLO para el universo curado
(`research.bcra_watch`, seed abajo con IDs VERIFICADOS contra el catálogo vivo
2026-07-18). Eficiencia primero: incremental por watermark, nada de bajar 1.581
variables ni re-bajar historia que ya está.

Modos:
    --dry-run    universo + watermarks + qué haría (0 requests de series)
    --backfill   historia COMPLETA de cada serie del watch (una vez; throttled)
    (default)    incremental: desde = max(fecha) − 7d por serie (captura las
                 revisiones retroactivas del BCRA) — un run sin datos nuevos
                 escribe 0 filas. Refresca el catálogo 1×/corrida (2 requests).

Cron: 4×/día L-S (deploy/crontab.txt) — el BCRA publica 1×/día hábil con rezago
de 1-2 días; la vista sirve SIEMPRE de la DB (24/7 real).
"""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

from core import bcra_api
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Universo curado inicial — IDs VERIFICADOS contra el catálogo v4 (2026-07-18).
# (bloque, id, etiqueta, unidad, orden). Editable después en research.bcra_watch.
_SEED: list[tuple[str, int, str, str, int]] = [
    ("reservas",     1,  "Reservas internacionales",      "M USD", 1),
    ("tipo_cambio",  5,  "Mayorista A3500",               "ARS",   1),
    ("tipo_cambio",  4,  "Minorista (prom. vendedor)",    "ARS",   2),
    ("tasas",        7,  "BADLAR bancos privados",        "%",     1),
    ("tasas",        44, "TAMAR bancos privados",         "%",     2),
    ("tasas",        8,  "TM20 bancos privados",          "%",     3),
    ("tasas",        11, "BAIBAR (interbancaria)",        "%",     4),
    ("tasas",        12, "Plazo fijo 30 días",            "%",     5),
    ("tasas",        13, "Adelantos en cta. cte.",        "%",     6),
    ("tasas",        14, "Préstamos personales",          "%",     7),
    ("dinero",       15, "Base monetaria",                "M ARS", 1),
    ("dinero",       16, "Circulación monetaria",         "M ARS", 2),
    ("dinero",       17, "Billetes en poder del público", "M ARS", 3),
    ("dinero",       26, "Préstamos al sector privado",   "M ARS", 4),
    ("inflacion",    27, "Inflación mensual",             "%",     1),
    ("inflacion",    28, "Inflación interanual",          "%",     2),
    ("inflacion",    29, "REM: inflación esperada 12m",   "%",     3),
    ("indexacion",   30, "CER",                           "índice", 1),
    ("indexacion",   31, "UVA",                           "índice", 2),
    ("depositos",    21, "Depósitos ARS (total)",         "M ARS", 1),
    ("depositos",    24, "Depósitos ARS a plazo",         "M ARS", 2),
    ("depositos",    103, "Depósitos USD púb.+priv.",     "M USD", 3),
    ("depositos",    104, "Depósitos USD privados",       "M USD", 4),
]

_VENTANA_INCREMENTAL_D = 7  # re-lee 7 días: el BCRA revisa datos hacia atrás


def _seed_watch(cur) -> None:
    """Siembra el watch si está vacío (idempotente; lo editado a mano manda)."""
    cur.execute("SELECT count(*) FROM research.bcra_watch")
    if cur.fetchone()[0]:
        return
    for bloque, idv, etiqueta, unidad, orden in _SEED:
        cur.execute(
            "INSERT INTO research.bcra_watch (id_variable, bloque, etiqueta, unidad, orden)"
            " VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id_variable) DO NOTHING",
            (idv, bloque, etiqueta, unidad, orden),
        )
    logger.info("bcra_research: watch sembrado con %s series", len(_SEED))


def _watch(cur) -> list[dict]:
    cur.execute("SELECT id_variable, bloque, etiqueta FROM research.bcra_watch "
                "WHERE activo ORDER BY bloque, orden")
    return [{"id": r[0], "bloque": r[1], "etiqueta": r[2]} for r in cur.fetchall()]


def _watermarks(cur) -> dict[int, str]:
    cur.execute("SELECT id_variable, max(fecha) FROM research.bcra_series GROUP BY id_variable")
    return {r[0]: r[1].isoformat() for r in cur.fetchall() if r[1]}


def _upsert_puntos(cur, id_variable: int, puntos: list[dict]) -> int:
    if not puntos:
        return 0
    cur.executemany(
        "INSERT INTO research.bcra_series (id_variable, fecha, valor) VALUES "
        f"({id_variable}, %(fecha)s, %(valor)s) "
        "ON CONFLICT (id_variable, fecha) DO UPDATE SET valor = EXCLUDED.valor, "
        "ingestado_en = now()",
        puntos,
    )
    return len(puntos)


def _refrescar_catalogo(cur) -> int:
    """Espejo del catálogo (metadata de las 1.581 — 2 requests, chico)."""
    cat = bcra_api.catalogo()
    for v in cat:
        cur.execute(
            "INSERT INTO research.bcra_variables (id_variable, descripcion, categoria,"
            " periodicidad, unidad, moneda, primer_fecha, ultima_fecha, ultimo_valor)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (id_variable) DO UPDATE SET descripcion=EXCLUDED.descripcion,"
            " categoria=EXCLUDED.categoria, ultima_fecha=EXCLUDED.ultima_fecha,"
            " ultimo_valor=EXCLUDED.ultimo_valor, actualizado_en=now()",
            (v.get("idVariable"), v.get("descripcion"), v.get("categoria"),
             v.get("periodicidad"), v.get("unidadExpresion"), v.get("moneda"),
             v.get("primerFechaInformada") or None, v.get("ultFechaInformada") or None,
             v.get("ultValorInformado")),
        )
    return len(cat)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="historia completa de cada serie del watch (una vez)")
    ap.add_argument("--dry-run", action="store_true",
                    help="muestra qué haría; no pega series ni escribe")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    hoy = datetime.now(UTC).date()
    with get_pool().connection() as conn, conn.cursor() as cur:
        _seed_watch(cur)
        series = _watch(cur)
        marcas = _watermarks(cur)

    print(f"Watch: {len(series)} series · con historia: {len(marcas)}")
    if args.dry_run:
        for s in series:
            wm = marcas.get(s["id"])
            desde = ("TODO (backfill)" if args.backfill or not wm
                     else (datetime.fromisoformat(wm).date()
                           - timedelta(days=_VENTANA_INCREMENTAL_D)).isoformat())
            print(f"  [{s['bloque']:12}] {s['id']:4} {s['etiqueta']:34} desde={desde}")
        print("(DRY-RUN — no se pegó a la API de series ni se escribió)")
        return

    from core.job_runs import JobRunLogger
    with JobRunLogger("bcra_research") as jr:
        n_total = n_series_ok = 0
        with get_pool().connection() as conn, conn.cursor() as cur:
            try:
                n_cat = _refrescar_catalogo(cur)
                jr.log(f"catálogo refrescado: {n_cat} variables")
            except Exception as e:
                logger.warning("bcra_research: catálogo falló (%s) — sigo con series", e)
                jr.log(f"catálogo ERROR: {e}")
            for s in series:
                wm = marcas.get(s["id"])
                desde = None if (args.backfill or not wm) else (
                    datetime.fromisoformat(wm).date()
                    - timedelta(days=_VENTANA_INCREMENTAL_D)
                ).isoformat()
                try:
                    puntos = bcra_api.serie(s["id"], desde=desde, hasta=hoy.isoformat())
                    n = _upsert_puntos(cur, s["id"], puntos)
                except Exception as e:
                    logger.warning("bcra_research: serie %s (%s) falló (%s)",
                                   s["id"], s["etiqueta"], e)
                    jr.log(f"serie {s['id']} {s['etiqueta']}: ERROR {e}")
                    continue
                n_total += n
                n_series_ok += 1
        jr.set_stat("series_ok", n_series_ok)
        jr.set_stat("puntos", n_total)
        jr.set_stat("modo", "backfill" if args.backfill else "incremental")
    print(f"✅ {n_series_ok}/{len(series)} series · {n_total} puntos upserteados "
          f"({'backfill' if args.backfill else 'incremental'})")


if __name__ == "__main__":
    main()
