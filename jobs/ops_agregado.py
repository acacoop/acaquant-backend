"""jobs/ops_agregado.py — mantiene el agregado diario HOT/COLD de operaciones.

Decisión user 2026-08-04 (revierte el "no precomputes" del decomiso del viejo
ops_rollup, que recalculaba a ciegas y drifteaba): los días CERRADOS son casi
inmutables pero NO del todo — backfills (aranceles, tasa MAV, re-ingestas)
tocan boletos viejos. Por eso este job NO appendea a ciegas: recomputa POR DÍA
SUCIO. Un día está sucio si tiene algún boleto con `ingestado_en` posterior al
último cálculo de ese día en `operaciones.ops_agregado_diario` — así cualquier
corrección histórica re-agrega su día entero y el agregado NUNCA puede driftear
de la tabla fuente.

Por cada día sucio se recalculan las 3 variantes de `moneda_calc` (ARS / USD /
USD_DOL) con LAS MISMAS expresiones que usan las vistas en vivo
(operaciones_sql._bruto_expr/_arancel_expr/_ops_where sin filtros) — el número
del agregado es por construcción el mismo que daría la query en vivo.

Consumidor: operaciones_sql.ops_serie (historia del agregado + HOY en vivo).
HOY nunca se lee del agregado → el intradía siempre es fresco.

REGLA #4: scopeado (solo días sucios), batcheado con sleep, idempotente
(DELETE+INSERT por día), vía run_job.sh.

Uso:
    python -m jobs.ops_agregado            # días sucios (incremental)
    python -m jobs.ops_agregado --full     # backfill completo (primera vez)

Cron sugerido: 22:15 UTC L-V (después de la última pasada de la cadena negocio).
"""
from __future__ import annotations

import sys
import time

from core.job_runs import JobRunLogger
from core.postgres import get_job_pool

_MONEDAS = ("ARS", "USD", "USD_DOL")
_BATCH = 30          # días por lote
_SLEEP_S = 0.5       # respiro entre lotes (REGLA #4)


def _exprs(moneda: str) -> tuple[str, str, str, str]:
    """(where_bruto, expr_bruto, where_arancel, expr_arancel) — importadas del
    service para que el agregado use LAS MISMAS fórmulas que la vista en vivo."""
    from api.services.operaciones_sql import _arancel_expr, _bruto_expr, _ops_where

    where_b, _p_b = _ops_where(moneda)
    where_a, _p_a = _ops_where(moneda, arancel=True)
    # _ops_where con solo `moneda` no parametriza nada más que %(moneda)s.
    return where_b, _bruto_expr(moneda), where_a, _arancel_expr(moneda)


def _dias_sucios(cur, full: bool) -> list:
    if full:
        cur.execute("SELECT DISTINCT concertacion FROM operaciones.operaciones "
                    "WHERE concertacion IS NOT NULL ORDER BY concertacion")
        return [r[0] for r in cur.fetchall()]
    # Día sucio = tiene boletos ingresados/tocados DESPUÉS del último cálculo
    # de ese día (o nunca calculado). ix_ops_ingestado acota el scan.
    cur.execute(
        "SELECT DISTINCT o.concertacion FROM operaciones.operaciones o "
        "LEFT JOIN operaciones.ops_agregado_diario a "
        "  ON a.fecha = o.concertacion AND a.moneda_calc = 'ARS' "
        "WHERE o.concertacion IS NOT NULL "
        "  AND (a.fecha IS NULL OR o.ingestado_en IS NULL "
        "       OR o.ingestado_en > a.actualizado_en "
        "       OR o.anulado_en > a.actualizado_en) "
        "ORDER BY o.concertacion")
    return [r[0] for r in cur.fetchall()]


def _recomputar_dia(cur, dia) -> None:
    """DELETE + INSERT de las 3 variantes del día. Idempotente."""
    cur.execute("DELETE FROM operaciones.ops_agregado_diario WHERE fecha = %s", (dia,))
    for moneda in _MONEDAS:
        where_b, expr_b, where_a, expr_a = _exprs(moneda)
        cur.execute(
            f"INSERT INTO operaciones.ops_agregado_diario "
            f"(fecha, moneda_calc, bruto, arancel, n_boletos, actualizado_en) "
            f"SELECT %(dia)s, %(mon)s, "
            f"COALESCE((SELECT {expr_b} FROM operaciones.operaciones "
            f"          WHERE concertacion = %(dia)s AND {where_b}), 0), "
            f"COALESCE((SELECT {expr_a} FROM operaciones.operaciones "
            f"          WHERE concertacion = %(dia)s AND {where_a}), 0), "
            f"(SELECT count(*) FROM operaciones.operaciones "
            f" WHERE concertacion = %(dia)s AND anulado_en IS NULL), now()",
            {"dia": dia, "mon": moneda, "moneda": moneda},
        )


def run(full: bool = False) -> int:
    with JobRunLogger("ops_agregado") as jr:
        pool = get_job_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            dias = _dias_sucios(cur, full)
        jr.set_stat("dias_sucios", len(dias))
        if not dias:
            jr.log("Agregado al día — nada que recomputar.")
            return 0
        jr.log(f"Recomputando {len(dias)} día(s) "
               f"({dias[0]} → {dias[-1]}){' [FULL]' if full else ''}")
        hechos = 0
        for i in range(0, len(dias), _BATCH):
            lote = dias[i:i + _BATCH]
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    for d in lote:
                        _recomputar_dia(cur, d)
                conn.commit()
            hechos += len(lote)
            if i + _BATCH < len(dias):
                time.sleep(_SLEEP_S)
        jr.set_stat("dias_recomputados", hechos)
        jr.log(f"OK — {hechos} día(s) × {len(_MONEDAS)} monedas.")
    return 0


if __name__ == "__main__":
    sys.exit(run(full="--full" in sys.argv))
