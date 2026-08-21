"""LEER EL CÓDIGO QUE SE EJECUTA, NO EL QUE SE EXPLICA.

Varios tests de este repo verifican una decisión leyendo el fuente (que una
guarda esté, que dos caminos usen la misma función, que un `except` envuelva a
tal cosa). Es una técnica útil y tiene UNA trampa que ya se comió cuatro tests
en una semana:

    # ⚠️ Antes esto decía `if hallazgos:` y cerraba de más.
    if evaluados:
        ...

Un `assert "if hallazgos:" not in src` **falla**, porque el comentario que
explica el bug arreglado nombra el bug arreglado. Y cuanto mejor documentado
está el código, más probable es que pase.

> Un test que lee el fuente tiene que leer lo que se EJECUTA.
"""
from __future__ import annotations

import inspect


def codigo(obj) -> str:
    """El fuente sin comentarios ni docstring."""
    src = inspect.getsource(obj)
    sin_com = "\n".join(x.split("#")[0].rstrip() for x in src.splitlines())
    # El docstring también: explica el porqué, no es lo que corre.
    for comilla in ('"""', "'''"):
        while sin_com.count(comilla) >= 2:
            a = sin_com.index(comilla)
            b = sin_com.index(comilla, a + 3)
            sin_com = sin_com[:a] + sin_com[b + 3:]
    return sin_com
