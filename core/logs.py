"""core/logs.py — UN SOLO FORMATO DE LOG, con el NIVEL adentro.

Doc madre: **`docs/AV_AGENT.md`** §0.ac.

**Por qué existe.** Buscando errores en los logs de los motores aparecieron cero
en 24 horas, y no porque no hubiera: porque *no había forma de encontrarlos*.

    · Ninguna unit de systemd declara nivel (`SyslogLevelPrefix`), así que
      **journald marca TODAS las líneas como `info`** — incluidas las de
      `logger.error`. Filtrar por `-p warning` devuelve vacío siempre.
    · Y 9 de 13 motores formateaban con `"%(asctime)s %(message)s"`, **sin el
      nombre del nivel**, así que el texto tampoco lo decía.
    · `engines/valores.py` (motor_rofex, el feed de precios de la mesa) **no
      configuraba logging en absoluto**: el logger raíz quedaba sin handlers y
      en WARNING, o sea que sus `logger.info` se DESCARTABAN y sus `logger.error`
      salían por el handler de último recurso, a stderr y sin fecha.

Juntando las tres, un error de un motor era indistinguible de una línea normal —
para el agente y para una persona leyendo `journalctl`. Un log donde no se puede
encontrar un error no es un log, es ruido que ocupa disco.

**La regla que queda: el nivel viaja EN EL TEXTO.** Es lo único que sobrevive a
journald, a `tail`, a un `grep` y a un copiar-pegar en un chat. Se podría además
mandarle el nivel a systemd con el prefijo `<N>`, pero eso solo lo entiende
journald: el texto lo entiende todo el mundo.

Uso — **solo desde un entrypoint** (motor, job, daemon), nunca desde una librería:

    from core.logs import configurar
    logger = configurar("MotorCurvas")
"""
from __future__ import annotations

import logging

# `levelname` es lo que hace encontrable un error, y `name` lo que dice CUÁL de
# las piezas del proceso lo tiró (un motor corre varios módulos adentro).
FORMATO = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configurar(nombre: str = "", *, nivel: int = logging.INFO) -> logging.Logger:
    """Deja el logging listo y devuelve el logger de `nombre`.

    `force=True` PISA lo que haya configurado antes. Es a propósito: una
    librería importada puede haber llamado a `basicConfig` primero y dejado el
    formato viejo, y entonces el motor volvería a escribir líneas sin nivel sin
    que nada falle — que es exactamente el problema que este módulo resuelve.
    """
    logging.basicConfig(level=nivel, format=FORMATO, force=True)
    return logging.getLogger(nombre or "app")
