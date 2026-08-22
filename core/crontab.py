"""core/crontab.py — el parser del crontab, UNA sola vez y sin dependencias.

Vivía adentro de `api.services.jobs_catalogo` y lo necesitaba también
`core.dependencias` (para traducir el label de un cron a sus proveedores).
Un import core → api rompe el contrato de capas (`.importlinter`: core/ no
importa nada del proyecto), y copiar el parser habría dejado dos regexes que
se desincronizan solas (REGLA #9). La pieza es parsing PURO de un archivo que
viaja con el deploy — no toca base ni red — así que su lugar es core/;
`jobs_catalogo` delega acá y todos sus llamadores siguen igual.
"""
from __future__ import annotations

import re
from pathlib import Path

CRONTAB = Path(__file__).resolve().parents[1] / "deploy" / "crontab.txt"

_RE_RUNJOB = re.compile(r"run_job\.sh\s+(\S+)\s+(\S+)\s+'(.+)'\s*$")
_RE_MODULES = re.compile(r"-m\s+((?:jobs|engines|scripts)\.[\w.]+)")


def parse_crontab() -> list[dict]:
    """Líneas run_job.sh del crontab → [{label, schedule, timeout, modules}]."""
    out: list[dict] = []
    for raw in CRONTAB.read_text(encoding="utf-8").splitlines():
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
