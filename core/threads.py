"""threads.py — lanzamiento de hilos VITALES en motores.

Problema que resuelve (AUDITORIA A4): los motores lanzan threads daemon
(`_snapshot_loop`, `_flush_loop`, `_worker_loop`). Si uno crashea con una
excepción no atrapada, el hilo muere PERO el proceso sigue vivo → systemd
ve "active" y el motor queda zombie: vivo para el sistema, muerto para el
mercado (snapshots congelados sin ninguna alerta). Es el modo de falla de
los incidentes de motores stale.

Solución: todo hilo del que depende la salida del motor se lanza con
`lanzar_hilo_vital`. Si el target muere por excepción no atrapada:
  1. se loguea CRITICAL con el traceback completo, y
  2. se mata el proceso entero (os._exit) → systemd lo reinicia en 10s
     (las 13 units de motores tienen Restart=always + RestartSec=10) y el
     arranque en frío repuebla el estado.

Morir ruidosamente + renacer limpio > sobrevivir zombie.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

logger = logging.getLogger("HiloVital")


def lanzar_hilo_vital(
    target: Callable, nombre: str, *, args: tuple = (), daemon: bool = True,
) -> threading.Thread:
    """Lanza `target` en un thread; si muere por excepción, mata el proceso.

    Args:
        target: callable del loop (corre indefinidamente).
        nombre: nombre del hilo para el log y para `threading.Thread(name=)`.
        args: args posicionales para el target.
        daemon: igual que threading.Thread (default True, como los motores).
    """
    def _guardia():
        try:
            target(*args)
        except Exception:
            logger.critical(
                "HILO VITAL '%s' MURIÓ — se mata el proceso para que systemd "
                "lo reinicie (Restart=always). Traceback:", nombre, exc_info=True,
            )
            # os._exit (no sys.exit): estamos en un thread no-main y queremos
            # terminar el proceso YA, sin depender de que el main loop coopere.
            os._exit(1)

    t = threading.Thread(target=_guardia, name=nombre, daemon=daemon)
    t.start()
    return t
