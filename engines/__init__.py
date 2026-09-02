"""engines/ — los motores de mercado (always-on L-V en rueda).

⚠️ **EL LATIDO ARRANCA SOLO ACÁ** (`core/latido.py`, §0.da). Cuando el proceso
es `python -m engines.<motor>`, este paquete se importa antes que el módulo del
motor, así que es el único lugar por el que pasan TODOS —incluido el que se
escriba mañana— sin que nadie tenga que acordarse de llamar a nada. Un motor
nuevo late desde su primer arranque y el agente lo espera por su unit de
systemd (`agente/unidades.py`).

Se usa `sys.orig_argv` y no `sys.argv`: mientras se importa el paquete,
`sys.argv[0]` todavía vale `-m`.
"""
from __future__ import annotations


def _latido_automatico() -> None:
    import sys
    if "pytest" in sys.modules:
        return
    argv = getattr(sys, "orig_argv", None) or []
    proceso = next((argv[i + 1] for i, a in enumerate(argv)
                    if a == "-m" and i + 1 < len(argv)
                    and argv[i + 1].startswith("engines.")), None)
    if not proceso:
        return
    try:
        from core import latido
        latido.arrancar(proceso)
    except Exception as e:  # nunca puede impedir que un motor arranque
        import logging
        logging.getLogger(__name__).warning("latido automático no arrancó: %s", e)


_latido_automatico()
