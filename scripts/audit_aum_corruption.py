"""audit_aum_corruption.py — identifica (fecha_snapshot, id_cuenta) con
data faltante en Valuaciones.AuM tras un re-run con timeouts.

Combina dos detecciones:

(1) LOG PARSING — opcional, vía --log /path/to/log
    Lee el log capturado del backfill (tmux capture-pane).
    Trackea la fecha "actual" desde las líneas
    `📅 fecha_snapshot=YYYY-MM-DD ...` y, para cada
    `[idx/total] [cuenta_id] ❌ Error: ...`, registra el par
    (fecha_actual, cuenta_id). Solo "❌ Error" — "sin datos" no
    cuenta como corrupción (la cuenta legítimamente no tenía
    posiciones ese día).

(2) GAP DETECTION — siempre activo, sobre Mongo
    Para cada cuenta, mira sus fechas presentes en AuM. Para
    cualquier fecha entre su primera y última observación que NO
    aparezca, registra el par como "gap" — la cuenta debería
    haber estado pero no está. Filtra fechas con corrupción
    masiva (>--max-gap-pct de cuentas faltantes — probable
    falla del job entero, no per-cuenta).

OUTPUT — CSV en --out con columnas:
  fecha_snapshot,id_cuenta,sources
  2025-09-15,633,log
  2025-09-15,805,log+gap
  2025-09-15,1007,gap

Sources combinados: si un par es detectado por ambos métodos,
se reporta como "log+gap" — es la confirmación más fuerte.

Uso:
  python -m scripts.audit_aum_corruption \\
      --log /tmp/aum-backfill.log --out /tmp/aum-corruption.csv

  python -m scripts.audit_aum_corruption \\
      --out /tmp/aum-corruption.csv      # solo gap detection
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, ".")

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("audit_aum_corruption")

DB_NAME = "Valuaciones"
COL_NAME = "AuM"

# Regex stateful: trackea la fecha "actual" en el log.
_RE_FECHA = re.compile(r"fecha_snapshot=(\d{4}-\d{2}-\d{2})")
# Match "[idx/total] [cuenta_id] ❌ Error" — espacios laterales tolerados.
_RE_ERROR = re.compile(r"\[\s*\d+\s*/\s*\d+\s*\]\s+\[\s*(\d+)\s*\]\s+❌\s*Error")


def parse_log(path: Path) -> set[tuple[str, str]]:
    """Devuelve set de (fecha_snapshot, id_cuenta) con error en el log."""
    if not path.exists():
        raise SystemExit(f"Log no existe: {path}")
    pares: set[tuple[str, str]] = set()
    fecha_actual: str | None = None
    n_lines = 0
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            n_lines += 1
            m_fecha = _RE_FECHA.search(line)
            if m_fecha:
                fecha_actual = m_fecha.group(1)
                continue
            m_err = _RE_ERROR.search(line)
            if m_err and fecha_actual:
                cuenta_id = m_err.group(1)
                pares.add((fecha_actual, cuenta_id))
    logger.info("Log parsing: %d líneas leídas, %d pares con error",
                n_lines, len(pares))
    return pares


def find_gaps(max_gap_pct: float) -> tuple[set[tuple[str, str]], dict[str, int]]:
    """Detecta gaps por cuenta. Cualquier fecha entre la primera y la
    última observación de una cuenta que no esté presente = gap.

    Filtra fechas con corrupción masiva: si más de max_gap_pct de las
    cuentas activas en el período están missing en una fecha, asumimos
    que es falla del job entero (no per-cuenta) y skipeamos esa fecha
    del análisis.

    Returns:
        (pares_gap, stats) donde stats es {fecha: n_missing} para las
        fechas filtradas (auditable).
    """
    client = get_mongo_client()
    col = client[DB_NAME][COL_NAME]

    # 1. Para cada cuenta, conjunto de fechas en que aparece.
    pipeline = [
        {"$group": {
            "_id":     "$id_cuenta",
            "fechas":  {"$addToSet": "$fecha_snapshot"},
        }},
    ]
    cuenta_fechas: dict[str, set[str]] = {}
    all_fechas: set[str] = set()
    for d in col.aggregate(pipeline):
        cuenta = d.get("_id")
        if not cuenta:
            continue
        fset = {f for f in d.get("fechas", []) if f}
        if not fset:
            continue
        cuenta_fechas[cuenta] = fset
        all_fechas.update(fset)

    if not all_fechas:
        return set(), {}

    sorted_fechas = sorted(all_fechas)
    logger.info("Gap detection: %d cuentas con presencia, %d fechas únicas",
                len(cuenta_fechas), len(sorted_fechas))

    # 2. Para cada cuenta, encontrar gaps en su rango de actividad.
    gap_pares: set[tuple[str, str]] = set()
    for cuenta, fset in cuenta_fechas.items():
        cf_sorted = sorted(fset)
        first = cf_sorted[0]
        last = cf_sorted[-1]
        # Dentro de [first, last], cualquier fecha en sorted_fechas no en fset = gap.
        for f in sorted_fechas:
            if f < first or f > last:
                continue
            if f not in fset:
                gap_pares.add((f, cuenta))

    # 3. Filtrar fechas con corrupción masiva (probable falla global).
    fecha_to_missing: dict[str, set[str]] = defaultdict(set)
    for fecha, cuenta in gap_pares:
        fecha_to_missing[fecha].add(cuenta)
    n_cuentas_total = len(cuenta_fechas)
    fechas_skip: dict[str, int] = {}
    threshold = int(n_cuentas_total * max_gap_pct)
    for fecha, missing in fecha_to_missing.items():
        if len(missing) > threshold:
            fechas_skip[fecha] = len(missing)
    if fechas_skip:
        logger.warning(
            "Skipping %d fechas con >= %.0f%% missing (probable falla global): %s",
            len(fechas_skip), max_gap_pct * 100, sorted(fechas_skip.keys()),
        )
        gap_pares = {p for p in gap_pares if p[0] not in fechas_skip}

    logger.info("Gap detection: %d pares (fecha,cuenta) detectados", len(gap_pares))
    return gap_pares, fechas_skip


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--log", type=Path,
                        help="Path al log capturado (opcional). Sin esto solo se hace gap detection.")
    parser.add_argument("--out", type=Path, required=True,
                        help="Path al CSV de salida (fecha_snapshot,id_cuenta,sources)")
    parser.add_argument("--max-gap-pct", type=float, default=0.30,
                        help="Si una fecha tiene > este pct de cuentas missing, "
                             "skipearla (probable falla global). Default 0.30 = 30%%.")
    args = parser.parse_args()

    log_pares: set[tuple[str, str]] = set()
    if args.log:
        logger.info("Parsing log: %s", args.log)
        log_pares = parse_log(args.log)

    logger.info("Running gap detection (max_gap_pct=%.2f)…", args.max_gap_pct)
    gap_pares, fechas_skip = find_gaps(args.max_gap_pct)

    # Combinar.
    todos = log_pares | gap_pares
    sources_map: dict[tuple[str, str], list[str]] = defaultdict(list)
    for p in log_pares:
        sources_map[p].append("log")
    for p in gap_pares:
        sources_map[p].append("gap")

    # Sort por fecha asc, cuenta asc para que el CSV sea estable.
    rows = sorted(todos, key=lambda p: (p[0], int(p[1]) if p[1].isdigit() else p[1]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["fecha_snapshot", "id_cuenta", "sources"])
        for fecha, cuenta in rows:
            sources = "+".join(sources_map[(fecha, cuenta)])
            writer.writerow([fecha, cuenta, sources])

    # Resumen.
    logger.info("══════════════════════════════════════════")
    logger.info("Total pares (fecha,cuenta) en CSV: %d", len(rows))
    logger.info("  · solo log:    %d", len(log_pares - gap_pares))
    logger.info("  · solo gap:    %d", len(gap_pares - log_pares))
    logger.info("  · log + gap:   %d", len(log_pares & gap_pares))
    if fechas_skip:
        logger.info("Fechas skippeadas (corrupción masiva): %d", len(fechas_skip))
        for fecha in sorted(fechas_skip):
            logger.info("  · %s: %d cuentas missing", fecha, fechas_skip[fecha])
    logger.info("CSV escrito en: %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
