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

from psycopg.rows import dict_row

from api.cache import cached

# El parser vive en core/ (lo necesita también core.dependencias, y core no
# puede importar de api). Se importa con el nombre que ya usaban todos los
# llamadores (`cat._parse_crontab()`), así ninguno se entera de la mudanza.
from core.crontab import parse_crontab as _parse_crontab
from core.postgres import get_pool

# label del cron / basename del módulo → tipo con el que loguea JobRunLogger,
# cuando NO coinciden (excepciones conocidas; el default es el basename).
_ALIAS_TIPO = {
    "portafolio_backfill": "aum",   # writer diario de tenencias (nombre legacy)
}


def _tipo_de(module: str) -> str:
    base = module.split(".")[-1]
    return _ALIAS_TIPO.get(base, base)


def schedules_por_modulo() -> dict[str, list[str]]:
    """`jobs.x` → los cron schedules con los que corre. **Sin tocar la base.**

    Existe para que cualquiera pueda contestar «¿cada cuánto corre esto?» sin
    hardcodear un horario: el crontab es la fuente y un horario copiado a mano en
    otro archivo se desincroniza el día que se cambia uno de los dos.
    """
    out: dict[str, list[str]] = {}
    for c in _parse_crontab():
        for mod in c["modules"]:
            out.setdefault(mod, []).append(c["schedule"])
    return out


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
