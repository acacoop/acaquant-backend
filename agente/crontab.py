"""agente/crontab.py — ¿EL CRON DEL REPO ES EL QUE CORRE?

Doc madre: **`docs/AV_AGENT.md`** §0.al.

EL AGUJERO
==========

`deploy/crontab.txt` dice, en su encabezado, que es la **fuente de verdad**. Y
todo el sistema le cree: `jobs_catalogo` lo parsea, `salud` arma un chequeo por
cada línea, `diagnostico_registry` valida el inventario contra él, la tab SKILLS
saca de ahí el horario de cada detector.

**Pero nadie lo compara nunca con el crontab REAL de la máquina.** Y `deploy.sh`
no lo instala —hace `git pull`, `apply_schema` y reinicia la API—, así que
agregar un cron nuevo al repo **no lo pone a correr**: hay que instalarlo a mano.

    en el repo   45 19 * * 1-5 … jobs.saldos_a_operadores     ✔ existe
    en la máquina                                             ¿?

Es REGLA #9(B) tal cual: **el mismo dato en dos lugares, sin árbitro y sin
chequeo**. Y falla del peor modo — el que este proyecto ya conoce: *no falla
nada*. El archivo está bien, el código está bien, los tests pasan, la pantalla
del agente muestra el job en su catálogo… y el job no corrió nunca. Nadie se
entera hasta que alguien pregunta «¿esto funcionó?», que es literalmente cómo
apareció.

QUÉ MIRA
========

Las dos direcciones, porque son dos problemas distintos:

    en el archivo y NO en la máquina  → el job NO CORRE y todos creen que sí
    en la máquina y NO en el archivo  → corre algo que el repo no declara: nadie
                                        lo revisa, y el día que se rompa nadie
                                        va a saber de dónde salió

⚠️ **Si no se puede leer el crontab, NO se afirma nada.** El agente no puede
concluir «no existe» desde una lectura que falló (§0.v): sin esto, un `crontab`
que no está en el PATH se leería como «ninguno instalado» y cantaría 40 jobs
caídos que están perfectos.
"""
from __future__ import annotations

import logging
import re
import subprocess

logger = logging.getLogger(__name__)

TIMEOUT_S = 10

# Solo se comparan las líneas que son un job de verdad. El crontab real también
# trae `MAILTO=`, `PATH=` y los `systemctl` de los motores; el archivo trae
# además comentarios. Lo que se compara es la ORDEN, no el archivo entero.
_ES_CRON = re.compile(r"^\s*[-\d*/,]+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S")


def _normalizar(linea: str) -> str:
    """Misma orden escrita distinto tiene que dar lo mismo. Solo se colapsan
    espacios: cambiar el horario o el comando SÍ es una diferencia real."""
    return " ".join((linea or "").split())


def _lineas(texto: str) -> set[str]:
    return {_normalizar(x) for x in (texto or "").splitlines()
            if x.strip() and not x.lstrip().startswith("#") and _ES_CRON.match(x)}


def del_repo() -> set[str]:
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[2] / "deploy" / "crontab.txt"
    try:
        return _lineas(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("agente/crontab: no pude leer %s (%s)", p, e)
        return set()


def de_la_maquina() -> set[str] | None:
    """El crontab instalado. **`None` = no pude mirar**, que NO es «está vacío»."""
    try:
        proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                              timeout=TIMEOUT_S)
    except Exception as e:
        logger.warning("agente/crontab: no pude correr `crontab -l` (%s)", e)
        return None
    # `crontab -l` sale 1 con "no crontab for root": ESO sí es un dato (no hay
    # ninguno instalado) y es distinto de que el comando no exista.
    if proc.returncode != 0:
        if "no crontab" in (proc.stderr or "").lower():
            return set()
        logger.warning("agente/crontab: `crontab -l` falló: %s",
                       (proc.stderr or "")[:160])
        return None
    return _lineas(proc.stdout)


def comparar() -> dict:
    """El estado crudo, para el detector y para el diag."""
    repo = del_repo()
    maquina = de_la_maquina()
    if maquina is None:
        return {"ok": False, "motivo": "no pude leer el crontab de la máquina",
                "en_repo": len(repo)}
    return {"ok": True, "en_repo": len(repo), "en_maquina": len(maquina),
            "sin_instalar": sorted(repo - maquina),
            "sin_declarar": sorted(maquina - repo)}


def detectar_crontab() -> list[dict]:
    """Hallazgos. **Nunca levanta**: corre con los demás detectores del sistema."""
    try:
        return _detectar()
    except Exception as e:
        logger.warning("agente/crontab: no pude comparar (%s)", e)
        return []


def _detectar() -> list[dict]:
    from agente.reloj import hhmm as _hhmm

    r = comparar()
    if not r["ok"]:
        # **El silencio se lee igual que un verde** (§0.s): si la prueba no
        # corrió, hay que decirlo.
        return [{
            "tipo": "cron_desalineado", "ticker": "crontab", "regla": "no_pude_mirar",
            "severidad": "baja",
            "motivo": f"no pude leer el crontab de la máquina · {_hhmm()}",
            "evidencia": {"texto": (
                "No sé si los crons del repo están instalados — **no es que estén "
                "mal**. Hasta que se pueda leer, ese control no está cubierto.")}}]

    out: list[dict] = []
    faltan, sobran = r["sin_instalar"], r["sin_declarar"]
    if faltan:
        out.append({
            "tipo": "cron_desalineado", "ticker": "crontab", "regla": "sin_instalar",
            "severidad": "alta",
            "motivo": (f"{len(faltan)} cron(s) del repo NO están en la máquina · "
                       f"no corren · {_hhmm()}"),
            "evidencia": {
                "texto": ("Están en `deploy/crontab.txt` y no en el crontab "
                          "instalado, así que **no se ejecutan**. No falla nada: "
                          "el catálogo los muestra igual.\nSe instalan con "
                          "`crontab /root/TradingAV/deploy/crontab.txt`."),
                "jobs": [_que_job(x) for x in faltan], "lineas": faltan[:20]}})
    if sobran:
        out.append({
            "tipo": "cron_desalineado", "ticker": "crontab", "regla": "sin_declarar",
            "severidad": "media",
            "motivo": (f"{len(sobran)} cron(s) corren y el repo no los declara · "
                       f"{_hhmm()}"),
            "evidencia": {
                "texto": ("Están instalados en la máquina y no en "
                          "`deploy/crontab.txt`. Corre algo que nadie revisa, y "
                          "la próxima instalación del archivo se los lleva "
                          "puestos sin avisar."),
                "jobs": [_que_job(x) for x in sobran], "lineas": sobran[:20]}})
    return out


def _que_job(linea: str) -> str:
    """De la línea entera, el nombre del job — que es lo único que se lee."""
    m = re.search(r"run_job\.sh\s+(\S+)", linea)
    if m:
        return m.group(1)
    m = re.search(r"-m\s+([\w.]+)", linea) or re.search(r"systemctl\s+\w+\s+(\S+)",
                                                        linea)
    return m.group(1) if m else _normalizar(linea)[:60]
