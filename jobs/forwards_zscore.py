"""forwards_zscore.py — coeficientes (media, desvío) por par de la matriz de forwards.

Lee Trading.ForwardsHistorico de los últimos 30 días hábiles disponibles, calcula
media y desvío muestral (n-1) de cada celda (ticker_largo, ticker_corto) de la
matriz por curva, y persiste SQL-NATIVE en mercado.forwards_zscore (write_native,
upsert por curva). Mongo Trading.ForwardsZscore YA NO se escribe (cutover SQL).

El z-score NO se persiste — el front lo computa en cada refresh con el live:
    z = (forward_live − media) / desvio

Así el numerador se mueve cada 30s con el motor live; el denominador queda fijo
hasta el próximo cron post-cierre. Pares con n_obs < 20 o desvio < 1e-6 se omiten
(el front los pinta como n/d).

Cron: 30 20 * * 1-5 (20:30 UTC = 17:30 ART, post-cierre del motor a 20:05 UTC).

Modos:
    (sin flag)              recalcula con el cierre disponible al momento de correr
    --dry                   no persiste, imprime resumen
"""
from __future__ import annotations

import argparse
import logging
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime

from core.job_runs import JobRunLogger
from core.postgres import get_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ForwardsZscore")

VENTANA_DIAS = 30
N_OBS_MIN = 20
DESVIO_MIN = 1e-6


def _curvas_disponibles() -> list[str]:
    """Curvas (k) presentes en ForwardsHistorico SQL (mercado.mercado_hist)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT k FROM mercado.mercado_hist "
                    "WHERE coleccion = 'ForwardsHistorico' AND k <> ''")
        return [r[0] for r in cur.fetchall() if r[0]]


def _stats_curva(curva: str) -> tuple[dict, dict]:
    """Devuelve (stats, meta). Lee los últimos VENTANA_DIAS de ForwardsHistorico
    desde SQL (mercado.mercado_hist, coleccion='ForwardsHistorico', k=curva).

    stats = {tk_largo: {tk_corto: {media, desvio, n_obs}}}
    meta  = {fechas_usadas: [...], n_dias: int}
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT data FROM mercado.mercado_hist "
            "WHERE coleccion = 'ForwardsHistorico' AND k = %s "
            "ORDER BY fecha DESC LIMIT %s",
            (curva, VENTANA_DIAS))
        docs = [r[0] for r in cur.fetchall()]
    if not docs:
        return {}, {"fechas_usadas": [], "n_dias": 0}

    # Recolecta todos los valores por par a lo largo de los días.
    series: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for d in docs:
        for tk_largo, inner in (d.get("matrix") or {}).items():
            for tk_corto, val in (inner or {}).items():
                if val is None:
                    continue
                try:
                    series[tk_largo][tk_corto].append(float(val))
                except (TypeError, ValueError):
                    continue

    stats: dict[str, dict[str, dict]] = {}
    descartados_n = 0
    descartados_desv = 0
    for tk_largo, inner in series.items():
        for tk_corto, vals in inner.items():
            n = len(vals)
            if n < N_OBS_MIN:
                descartados_n += 1
                continue
            media = statistics.fmean(vals)
            desvio = statistics.stdev(vals)  # muestral, n-1
            if desvio < DESVIO_MIN:
                descartados_desv += 1
                continue
            stats.setdefault(tk_largo, {})[tk_corto] = {
                "media": round(media, 8),
                "desvio": round(desvio, 8),
                "n_obs": n,
            }

    meta = {
        "fechas_usadas": sorted(d["fecha"] for d in docs),
        "n_dias": len(docs),
        "descartados_n_obs": descartados_n,
        "descartados_desvio": descartados_desv,
    }
    return stats, meta


def _procesar(
    curvas: list[str], ts: datetime, fecha_calculo: str, dry: bool,
    jr: JobRunLogger | None = None,
) -> None:
    for curva in curvas:
        stats, meta = _stats_curva(curva)
        n_pares = sum(len(inner) for inner in stats.values())
        logger.info(
            "[%s] dias=%d pares=%d descartados(n<%d)=%d descartados(desvio<%.0e)=%d",
            curva, meta["n_dias"], n_pares, N_OBS_MIN,
            meta["descartados_n_obs"], DESVIO_MIN, meta["descartados_desvio"],
        )
        if jr:
            jr.set_stat(curva, {"n_dias": meta["n_dias"], "n_pares": n_pares})
        if not stats:
            if jr:
                jr.error(f"{curva}: sin pares con historia suficiente")
            continue

        if dry:
            continue

        doc = {
            "curva": curva,
            "fecha_calculo": fecha_calculo,
            "updated_at": ts,
            "ventana_dias_habiles": VENTANA_DIAS,
            "n_obs_min": N_OBS_MIN,
            "stats": stats,
            "meta": meta,
        }
        # SQL-NATIVE (sin Mongo): coeficientes z-score por curva → mercado.forwards_zscore
        # (upsert incondicional por curva). Lo lee api/services/mercado_hist_sql.
        from core.pg_mirror import doc_iso, write_native
        write_native("forwards_zscore", ["curva"], [{"curva": curva, "data": doc_iso(doc)}])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="No persiste, solo imprime resumen")
    args = parser.parse_args()

    # Curvas presentes en histórico (SQL mercado.mercado_hist).
    curvas = _curvas_disponibles()
    if not curvas:
        logger.warning("ForwardsHistorico (mercado_hist) vacío — nada que calcular.")
        return 0

    ts = datetime.now(UTC)
    fecha_calculo = ts.date().isoformat()

    if args.dry:
        _procesar(curvas, ts, fecha_calculo, dry=True)
        logger.info("(--dry: no se escribió nada)")
        return 0

    with JobRunLogger("forwards_zscore") as jr:
        jr.set_stat("fecha_calculo", fecha_calculo)
        jr.set_stat("curvas", len(curvas))
        _procesar(curvas, ts, fecha_calculo, dry=False, jr=jr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
