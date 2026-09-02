"""core/latido.py — EL LATIDO: cada proceso que corre solo dice que está vivo, solo.

Hasta el 2026-09-02 solo `motor_ordenes` y `control_saldos` escribían un
latido, cada uno con su propio hilo y su propio formato, en una tabla singleton.
Los otros quince motores no: el agente deducía si estaban vivos mirando la
frescura de las tablas que escriben, y en un mercado quieto no podía distinguir
«vivo y sin operaciones» de «muerto». Ver `docs/AGENT.md` §0.da.

## Cómo funciona

Un hilo daemon escribe una fila en `operaciones.latidos` cada `CADA_S` segundos:
quién soy (`proceso` = lo que va después de `python -m`), mi pid, mi host,
cuándo arranqué, cuándo latí por última vez y un `data` libre con lo que el
proceso quiera contar (`anotar`, `sumar`, `marcar`). `core/websocket.py` anota
ahí el estado de la conexión y los símbolos rechazados, así que todo motor que
use el WS informa su feed sin escribir una línea.

## Cómo arranca — y por qué no hay que llamarlo

`engines/__init__.py` lo arranca solo cuando el proceso es `python -m
engines.<lo que sea>`: un motor nuevo late desde el primer día sin saber que
esto existe, y el agente lo descubre por la unidad de systemd. Los daemons de
`jobs/` (control_saldos, tenencia_live, agente) lo llaman explícito en su `main`.

## Lo que NUNCA hace

No levanta: si Postgres no responde, se loguea una vez cada 10 minutos y se
sigue latiendo. Un motor no puede morir por no poder decir que está vivo.
`LATIDO=0` en el entorno lo apaga (tests, corridas a mano).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

CADA_S = 15
TABLA = "operaciones.latidos"
_LOG_CADA_S = 600.0

_lock = threading.Lock()
_estado: dict = {"proceso": None, "arrancado_at": None, "data": {},
                 "hilo": None, "ultimo_error_log": 0.0, "escrituras": 0}


def proceso_de(orig_argv: list[str] | None = None) -> str | None:
    """`python -m engines.valores` → `engines.valores`. `None` si no hay `-m`.

    Usa `sys.orig_argv` (la línea de comando REAL): `sys.argv[0]` vale `-m`
    mientras se importa el paquete y recién después cambia al path del módulo.
    """
    argv = list(orig_argv if orig_argv is not None else getattr(sys, "orig_argv", None) or [])
    for i, a in enumerate(argv):
        if a == "-m" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def proceso_actual() -> str | None:
    return _estado["proceso"]


def anotar(**kv) -> None:
    """Suma pares al `data` del próximo latido (pisa la clave si ya estaba)."""
    with _lock:
        _estado["data"].update(kv)


def sumar(clave: str, n: int = 1) -> None:
    with _lock:
        _estado["data"][clave] = int(_estado["data"].get(clave, 0) or 0) + n


def marcar(clave: str) -> None:
    """`clave` = ahora, en ISO UTC. Para «última vez que pasó X»."""
    anotar(**{clave: datetime.now(UTC).isoformat()})


def agregar(clave: str, valores) -> None:
    """Acumula en una LISTA sin duplicar (símbolos rechazados, por ejemplo)."""
    with _lock:
        actual = list(_estado["data"].get(clave) or [])
        vistos = set(actual)
        for v in valores or []:
            if v not in vistos:
                actual.append(v)
                vistos.add(v)
        _estado["data"][clave] = actual


def _escribir() -> None:
    from core.postgres import get_pool
    with _lock:
        proceso = _estado["proceso"]
        arrancado = _estado["arrancado_at"]
        data = dict(_estado["data"])
    if not proceso:
        return
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {TABLA} (proceso, pid, host, arrancado_at, latido_at, data) "
            "VALUES (%s, %s, %s, %s, now(), %s::jsonb) "
            "ON CONFLICT (proceso) DO UPDATE SET "
            "  pid = EXCLUDED.pid, host = EXCLUDED.host, "
            "  arrancado_at = EXCLUDED.arrancado_at, latido_at = now(), "
            "  data = EXCLUDED.data",
            (proceso, os.getpid(), socket.gethostname(), arrancado,
             json.dumps(data, default=str)))
    with _lock:
        _estado["escrituras"] += 1


def latir_ahora() -> bool:
    """Un latido fuera de ritmo (antes de morir, por ejemplo). Nunca levanta."""
    try:
        _escribir()
        return True
    except Exception as e:
        _log_error(e)
        return False


def _log_error(e: Exception) -> None:
    ahora = time.monotonic()
    if ahora - _estado["ultimo_error_log"] >= _LOG_CADA_S:
        _estado["ultimo_error_log"] = ahora
        logger.warning("latido: no pude escribir %s (%s) — sigo latiendo", TABLA, e)


def _loop() -> None:
    while True:
        try:
            _escribir()
        except Exception as e:
            _log_error(e)
        time.sleep(CADA_S)


def arrancar(proceso: str | None = None) -> str | None:
    """Empieza a latir. Idempotente. Devuelve el nombre del proceso, o `None`
    si está apagado por entorno o no se pudo saber quién soy."""
    if os.environ.get("LATIDO", "1") == "0":
        return None
    nombre = proceso or proceso_de()
    if not nombre:
        return None
    with _lock:
        if _estado["hilo"] is not None:
            return _estado["proceso"]
        _estado["proceso"] = nombre
        _estado["arrancado_at"] = datetime.now(UTC)
        hilo = threading.Thread(target=_loop, name="latido", daemon=True)
        _estado["hilo"] = hilo
    hilo.start()
    logger.info("latido: %s late cada %ds en %s", nombre, CADA_S, TABLA)
    return nombre
