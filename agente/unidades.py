"""`agente/unidades.py` — qué procesos corre systemd, leído de donde se declara.

**El universo de motores no es una lista escrita a mano.** Sale de dos fuentes
que ya son la verdad del sistema: `deploy/systemd/*.service` (qué unidades hay
y qué módulo corre cada una) y `deploy/crontab.txt` (a qué hora las prende y
las apaga el cron). Un motor nuevo es una unit nueva, y con eso el agente ya lo
espera: no hay que declararlo en ningún lado más. Ver `docs/AGENT.md` §0.da.

`activas()` le pregunta a systemd en la máquina, igual que `agente/crontab.py`
le pregunta al crontab vivo: el agente corre en el mismo Droplet que los motores.
"""
from __future__ import annotations

import logging
import pathlib
import re
import subprocess
from datetime import datetime, timedelta

from agente import crontab

logger = logging.getLogger(__name__)

RAIZ = pathlib.Path(__file__).resolve().parents[1]
SYSTEMD = RAIZ / "deploy" / "systemd"
TIMEOUT_S = 10

_EXEC_M = re.compile(r"ExecStart=.*?-m\s+(\S+)")
_RESTART = re.compile(r"^Restart=(\S+)", re.M)
_CRON_UNIT = re.compile(
    r"^(\S+)\s+(\S+)\s+\S+\s+\S+\s+(\S+)\s+systemctl\s+(restart|start|stop)\s+(\S+?)\.service")


def _dias(expr: str) -> set[int] | None:
    """`1-5` → {0..4} (weekday de Python). `*` → None (todos)."""
    if expr == "*":
        return None
    out: set[int] = set()
    for parte in expr.split(","):
        if "-" in parte:
            a, b = parte.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(parte))
    # cron: 0/7 = domingo, 1 = lunes … Python: 0 = lunes.
    return {(d - 1) % 7 for d in out}


def _ventanas(lineas: set[str]) -> dict[str, dict]:
    """unidad → {inicio: (h, m), fin: (h, m), dias}. Solo las que el cron toca."""
    out: dict[str, dict] = {}
    for linea in lineas:
        m = _CRON_UNIT.match(linea)
        if not m:
            continue
        minuto, hora, dow, verbo, unidad = m.groups()
        try:
            hm = (int(hora), int(minuto))
        except ValueError:
            continue
        v = out.setdefault(unidad, {"inicio": None, "fin": None, "dias": _dias(dow)})
        if verbo in ("restart", "start"):
            v["inicio"] = hm
        else:
            v["fin"] = hm
    return out


def declaradas() -> dict[str, dict]:
    """unidad → {proceso, restart, ventana}. `proceso` es lo que va después de
    `-m` (`engines.valores`); `None` para lo que no corre por `-m` (uvicorn).
    `ventana` es `None` cuando el cron no la prende ni apaga: corre siempre."""
    out: dict[str, dict] = {}
    try:
        ventanas = _ventanas(crontab.del_repo())
        for f in sorted(SYSTEMD.glob("*.service")):
            txt = f.read_text(encoding="utf-8")
            m = _EXEC_M.search(txt)
            r = _RESTART.search(txt)
            out[f.stem] = {"proceso": m.group(1) if m else None,
                           "restart": r.group(1) if r else "",
                           "ventana": ventanas.get(f.stem)}
    except Exception as e:
        logger.warning("agente/unidades: no pude leer deploy/ (%s)", e)
        return {}
    return out


def activas(unidades: list[str]) -> dict[str, str] | None:
    """unidad → estado de `systemctl is-active` (active · inactive · failed ·
    activating…). `None` si no se pudo preguntar — que no es «todas apagadas»."""
    if not unidades:
        return {}
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", *[f"{u}.service" for u in unidades]],
            capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    except Exception as e:
        logger.warning("agente/unidades: systemctl no contestó (%s)", e)
        return None
    lineas = [x.strip() for x in (proc.stdout or "").splitlines()]
    if len(lineas) != len(unidades):
        return None
    return dict(zip(unidades, lineas, strict=True))


def en_ventana(v: dict | None, ahora: datetime) -> bool:
    """¿Debería estar corriendo a esta hora (UTC)? Sin ventana → siempre."""
    if not v or not v.get("inicio") or not v.get("fin"):
        return True
    dias = v.get("dias")
    if dias is not None and ahora.weekday() not in dias:
        return False
    hm = (ahora.hour, ahora.minute)
    return v["inicio"] <= hm < v["fin"]


def desde_inicio_s(v: dict | None, ahora: datetime) -> float | None:
    """Segundos desde el arranque de hoy según el cron. `None` sin ventana."""
    if not v or not v.get("inicio"):
        return None
    h, m = v["inicio"]
    inicio = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
    if inicio > ahora:
        inicio -= timedelta(days=1)
    return (ahora - inicio).total_seconds()
