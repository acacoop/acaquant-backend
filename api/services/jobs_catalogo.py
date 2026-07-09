"""api/services/jobs_catalogo.py — catálogo COMPLETO de jobs agendados.

Servicio PURO (sin FastAPI). La fuente de verdad es `deploy/crontab.txt` (viaja
con cada deploy): se parsea EN RUNTIME, así el catálogo nunca queda
desactualizado — un cron nuevo aparece solo, sin tocar listas a mano
("100% automático", pedido 2026-07-09). Se joinea con el último run por tipo
de `manager.job_runs` para mostrar estado/frescura.

Un cron puede encadenar varios módulos (ej. negocio_chain corre
negocio_movimientos && aranceles && fci_bilateral) → una fila por cron con el
último run de CADA módulo. Jobs sin JobRunLogger salen como "sin registro"
(instrumentado=false) — visibles igual, que es el punto.
"""
from __future__ import annotations

import re
from pathlib import Path

from psycopg.rows import dict_row

from api.cache import cached
from core.postgres import get_pool

_CRONTAB = Path(__file__).resolve().parents[2] / "deploy" / "crontab.txt"

# label del cron / basename del módulo → tipo con el que loguea JobRunLogger,
# cuando NO coinciden (excepciones conocidas; el default es el basename).
_ALIAS_TIPO = {
    "portafolio_backfill": "aum",   # writer diario de tenencias (nombre legacy)
}

_RE_RUNJOB = re.compile(r"run_job\.sh\s+(\S+)\s+(\S+)\s+'(.+)'\s*$")
_RE_MODULES = re.compile(r"-m\s+((?:jobs|engines|scripts)\.[\w.]+)")


def _parse_crontab() -> list[dict]:
    """Líneas run_job.sh del crontab → [{label, schedule, timeout, modules}]."""
    out: list[dict] = []
    for raw in _CRONTAB.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        schedule, cmd = " ".join(parts[:5]), parts[5]
        m = _RE_RUNJOB.search(cmd)
        if not m:
            continue  # systemctl start/stop de motores → viven en DIAGNÓSTICO
        label, timeout, inner = m.groups()
        modules = _RE_MODULES.findall(inner)
        out.append({"label": label, "schedule": schedule, "timeout": timeout,
                    "modules": modules})
    return out


def _tipo_de(module: str) -> str:
    base = module.split(".")[-1]
    return _ALIAS_TIPO.get(base, base)


@cached(ttl=30)
def catalogo_jobs() -> dict:
    """Catálogo completo: una fila por cron agendado + último run por módulo."""
    crons = _parse_crontab()
    tipos = sorted({_tipo_de(mod) for c in crons for mod in c["modules"]})
    ultimos: dict[str, dict] = {}
    if tipos:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT DISTINCT ON (tipo) tipo, status, started_at::text, "
                "finished_at::text, data FROM manager.job_runs "
                "WHERE tipo = ANY(%s) ORDER BY tipo, started_at DESC", (tipos,))
            for r in cur.fetchall():
                d = r.get("data") or {}
                errs = d.get("errors") or []
                ultimos[r["tipo"]] = {
                    "status": r["status"], "started_at": r["started_at"],
                    "finished_at": r["finished_at"],
                    "elapsed_s": d.get("elapsed_s"),
                    "resumen": (errs[0][:160] if errs else _stats_resumen(d.get("stats"))),
                }
    filas = []
    for c in crons:
        runs = []
        for mod in c["modules"]:
            tipo = _tipo_de(mod)
            runs.append({"modulo": mod, "tipo": tipo, "ultimo": ultimos.get(tipo)})
        filas.append({**c, "runs": runs,
                      "instrumentado": any(r["ultimo"] for r in runs)})
    return {"jobs": filas, "fuente": "deploy/crontab.txt"}


def _stats_resumen(stats) -> str:
    if not isinstance(stats, dict):
        return ""
    partes = [f"{k}={v}" for k, v in stats.items()
              if isinstance(v, (int, float, str))][:4]
    return " · ".join(str(p) for p in partes)
