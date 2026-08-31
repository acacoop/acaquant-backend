"""agente/crontab.py — ¿EL CRON DEL REPO ES EL QUE CORRE?

Doc madre: **`docs/AGENT.md`** §0.al.

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
    # ⚠️ `parents[1]`, no `[2]`: este módulo vivía en `api/services/` y al
    # mudarse a `agente/` subió un nivel. Con la ruta vieja buscaba en
    # `/root/deploy/crontab.txt` y el detector **no fallaba**: decía «no pude
    # leer el crontab», que es una respuesta legítima. Un error de ruta
    # disfrazado de degradación honesta es de los que duran meses.
    p = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "crontab.txt"
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


def que_job(linea: str) -> str:
    """De la línea entera, el nombre del job — que es lo único que se lee."""
    m = re.search(r"run_job\.sh\s+(\S+)", linea)
    if m:
        return m.group(1)
    m = re.search(r"-m\s+([\w.]+)", linea) or re.search(r"systemctl\s+\w+\s+(\S+)",
                                                        linea)
    return m.group(1) if m else _normalizar(linea)[:60]


def _horario(linea: str) -> str:
    """Los 5 campos de tiempo. `_ES_CRON` ya garantizó que están."""
    return " ".join(_normalizar(linea).split()[:5])


def sujeto(linea: str) -> str:
    """LA IDENTIDAD de un cron: **el job Y su horario**.

    ⚠️ El nombre del job SOLO no alcanza y no es una sutileza: medido sobre
    `deploy/crontab.txt` (95 líneas), **23 nombres están repetidos** — cada motor
    aparece dos veces (el `start` y el `stop`), `mayor_sync` y `sync_comitentes`
    tres. Si el sujeto fuera el nombre pelado, dos crons distintos compartirían
    el trío `habilidad+sujeto+regla`, y como `registro._ver` hace UPDATE sobre el
    abierto, **el segundo pisaría al primero en silencio**: el tablero mostraría
    uno y el otro no existiría para nadie.

    Con el horario adentro: 95 líneas → 95 sujetos, verificado por test.

    Y es la identidad correcta además de la única que funciona: cambiarle la hora
    a un cron ES otro cron —corre en otro momento y hay que instalarlo de nuevo—,
    así que el hallazgo viejo cierra por ausencia y nace el nuevo.
    """
    return f"{que_job(linea)} · {_horario(linea)}"
