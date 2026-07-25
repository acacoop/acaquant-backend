"""cleanup_retencion.py — aplica la RETENCIÓN que el schema ya prometía pero nadie ejecutaba.

Postgres no tiene TTL nativo (a diferencia de Mongo): la retención la tiene que
aplicar un cron. `sql/schema.sql` la documenta ("TTL 60d lo aplica el writer/cleanup",
"retención 45d: cron prune_native externo") pero ese cron NUNCA se escribió → las
tablas de log/auditoría crecían de forma monótona contra el techo de storage del plan
de Supabase. Este job es ese cron.

TABLAS Y TTL (columna de timestamp LEÍDA del schema, no adivinada):

    manager.job_runs           started_at     60d   schema.sql: "TTL 60d lo aplica el writer/cleanup"
    ia.trazas                  ts             90d   SIN TTL en el schema → default 90d (ver abajo)
    portafolio.backfill_log    actualizado    90d   SIN TTL en el schema → default 90d
    operaciones.ordenes_audit  ts            365d   SIN TTL en el schema → AUDITORÍA (ver abajo)
    manager.role_audit         ts            365d   SIN TTL en el schema → AUDITORÍA (ver abajo)

Donde el schema NO documenta un TTL el default es **90 días**. Las DOS excepciones
son las tablas de AUDITORÍA (`ordenes_audit` = rastro de órdenes enviadas al mercado,
`role_audit` = quién cambió permisos de quién): son append-only, chicas y su valor es
justamente poder mirar atrás, así que van a **365 días** — más conservador que el
default, a propósito. Bajarlo es una decisión de negocio, no técnica.

Las filas con la columna de timestamp en NULL NUNCA se borran (`col < cutoff` las
excluye): si no sabemos cuándo se escribió una fila, no la tocamos.

REGLA #4 del repo (un backfill a ciegas causó dos veces CPU 100%):
  - BATCHEADO: DELETE de a `--batch` filas (default 5000) vía ctid, nunca uno gigante.
    Cada lote es su propia transacción → cortar el job a la mitad no deja nada a medias
    y además ningún statement se acerca al `statement_timeout` (15s) del pool.
  - THROTTLE: `--sleep` (default 0.5s) entre lotes → no starva a los motores.
  - IDEMPOTENTE: re-correrlo no rompe nada (borra lo que quedó viejo, nada más).
  - FUERA DE RUEDA: lo garantiza el crontab (03:20 UTC = 00:20 ART).

Uso:
    python -m jobs.cleanup_retencion                    # borra (modo del cron)
    python -m jobs.cleanup_retencion --dry-run          # NO borra: cuenta e informa
    python -m jobs.cleanup_retencion --tabla ia.trazas  # una sola tabla (repetible)
    python -m jobs.cleanup_retencion --batch 2000 --sleep 1.0
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from core.job_runs import JobRunLogger
from core.postgres import get_pool, use_job_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("CleanupRetencion")

BATCH_DEFAULT = 5000
SLEEP_DEFAULT = 0.5
# Techo de vueltas por tabla: 2000 × 5000 = 10M filas por corrida. Si se alcanza, el job
# corta y avisa — la próxima corrida sigue donde quedó (es idempotente). Evita un loop
# infinito si algo devuelve rowcount raro.
MAX_VUELTAS = 2000


@dataclass(frozen=True)
class Retencion:
    tabla: str      # calificada con schema: `ia` NO está en el search_path del pool
    col: str        # columna de timestamp REAL de la tabla (leída de sql/schema.sql)
    dias: int
    nota: str


TABLAS: tuple[Retencion, ...] = (
    Retencion("manager.job_runs", "started_at", 60,
              "schema.sql: TTL 60d lo aplica el writer/cleanup"),
    Retencion("ia.trazas", "ts", 90,
              "sin TTL en el schema → default 90d"),
    Retencion("portafolio.backfill_log", "actualizado", 90,
              "sin TTL en el schema → default 90d"),
    Retencion("operaciones.ordenes_audit", "ts", 365,
              "sin TTL en el schema → AUDITORÍA: 365d (más conservador que el default)"),
    Retencion("manager.role_audit", "ts", 365,
              "sin TTL en el schema → AUDITORÍA: 365d (más conservador que el default)"),
)

_TABLAS_POR_NOMBRE = {r.tabla: r for r in TABLAS}


def _estimado_filas(cur, tabla: str) -> int:
    """Filas totales estimadas (pg_class.reltuples) — O(1), no escanea la tabla.
    Es una ESTIMACIÓN del planner (depende del último analyze), solo para el reporte."""
    cur.execute("SELECT reltuples::bigint FROM pg_class WHERE oid = %s::regclass", (tabla,))
    row = cur.fetchone()
    return max(int(row[0]), 0) if row and row[0] is not None else 0


def _contar(cur, r: Retencion, cutoff: datetime) -> int:
    # tabla/col salen del whitelist TABLAS (constantes del módulo) → no hay inyección.
    cur.execute(f"SELECT count(*) FROM {r.tabla} WHERE {r.col} < %s", (cutoff,))
    return int(cur.fetchone()[0])


def _borrar_en_lotes(r: Retencion, cutoff: datetime, batch: int, sleep_s: float) -> tuple[int, bool]:
    """Borra en lotes de `batch` filas. Devuelve (filas_borradas, cortado_por_tope).

    Cada lote abre su propia conexión/transacción y commitea al salir del `with` →
    el trabajo hecho queda firme aunque el job se corte en el medio.
    """
    total = 0
    sql = (
        f"DELETE FROM {r.tabla} WHERE ctid IN ("
        f"  SELECT ctid FROM {r.tabla} WHERE {r.col} < %s LIMIT %s"
        f")"
    )
    for _ in range(MAX_VUELTAS):
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (cutoff, batch))
            n = cur.rowcount or 0
        total += n
        if n < batch:
            return total, False
        time.sleep(sleep_s)
    return total, True


def run(dry: bool = False, tablas: list[str] | None = None,
        batch: int = BATCH_DEFAULT, sleep_s: float = SLEEP_DEFAULT) -> dict:
    objetivo = [_TABLAS_POR_NOMBRE[t] for t in tablas] if tablas else list(TABLAS)
    ahora = datetime.now(UTC)
    reporte: dict[str, dict] = {}

    modo = "DRY-RUN (no borra nada)" if dry else "BORRADO"
    logger.info("Retención — modo %s | batch=%d sleep=%.2fs", modo, batch, sleep_s)

    for r in objetivo:
        cutoff = ahora - timedelta(days=r.dias)
        t0 = time.perf_counter()

        with get_pool().connection() as conn, conn.cursor() as cur:
            candidatas = _contar(cur, r, cutoff)
            estimado = _estimado_filas(cur, r.tabla)

        if dry:
            dt = time.perf_counter() - t0
            reporte[r.tabla] = {
                "dias": r.dias, "col": r.col, "candidatas": candidatas,
                "borradas": 0, "total_estimado": estimado, "segundos": round(dt, 2),
            }
            logger.info(
                "[DRY] %-26s ttl=%3dd  borraría %7d filas (de ~%d estimadas)  [%s]",
                r.tabla, r.dias, candidatas, estimado, r.nota,
            )
            continue

        borradas, cortado = _borrar_en_lotes(r, cutoff, batch, sleep_s)
        dt = time.perf_counter() - t0
        reporte[r.tabla] = {
            "dias": r.dias, "col": r.col, "candidatas": candidatas,
            "borradas": borradas, "total_estimado": estimado,
            "segundos": round(dt, 2), "cortado_por_tope": cortado,
        }
        logger.info(
            "%-26s ttl=%3dd  borradas %7d filas en %.1fs%s",
            r.tabla, r.dias, borradas, dt,
            "  ⚠️ CORTADO POR TOPE (sigue en la próxima corrida)" if cortado else "",
        )

    return reporte


def main() -> int:
    p = argparse.ArgumentParser(description="Aplica la retención de las tablas de log/auditoría.")
    p.add_argument("--dry-run", action="store_true",
                   help="cuenta cuántas filas borraría por tabla, SIN borrar nada")
    p.add_argument("--tabla", action="append", choices=sorted(_TABLAS_POR_NOMBRE),
                   help="limitar a una tabla (repetible). Default: todas")
    p.add_argument("--batch", type=int, default=BATCH_DEFAULT, help="filas por lote de DELETE")
    p.add_argument("--sleep", type=float, default=SLEEP_DEFAULT, help="pausa entre lotes (s)")
    args = p.parse_args()

    # Carril de jobs (pool aislado): un cleanup de fondo NUNCA compite con la web.
    with JobRunLogger("cleanup_retencion") as jr, use_job_pool():
        reporte = run(dry=args.dry_run, tablas=args.tabla,
                      batch=args.batch, sleep_s=args.sleep)
        jr.set_stat("dry", args.dry_run)
        jr.set_stat("tablas", reporte)
        jr.set_stat("borradas_total", sum(v["borradas"] for v in reporte.values()))
        if any(v.get("cortado_por_tope") for v in reporte.values()):
            jr.error("alguna tabla se cortó por el tope de vueltas — re-correr el job")
    return 0


if __name__ == "__main__":
    sys.exit(main())
