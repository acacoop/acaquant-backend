"""EL DIAG DE AHORA NO PUEDE QUEDARSE VIEJO.

`scripts/diag_ahora` existe para una sola cosa: que lo que se ve por consola
**no pueda diferir** de lo que dibuja la pantalla. Llama a la misma función
(`av_agent_centinela.estado()`), así que el dato no puede discrepar…

…pero los BLOQUES estaban escritos a mano, y eso duró hasta el primero nuevo.
El 2026-08-24 se agregó `sin_confirmar` —los hallazgos que nadie pudo
verificar— y el diag siguió imprimiendo cuatro bloques como si nada: mostraba
«ROTO AHORA: 0» sin decir que había cuatro motores colgados al lado. El
silencio leyéndose como verde, en el script que existe justamente para evitar
eso.

Ahora los bloques se derivan del payload. Estos tests cuidan lo que queda:
que cada bloque tenga rótulo, y que ninguno pueda desaparecer.
"""
from __future__ import annotations

import inspect

import scripts.diag_ahora as d
from api.services import av_agent_centinela as cen


def _bloques_del_payload() -> set[str]:
    """Las listas que `_lo_de_hoy` devuelve, leídas de su `return`.

    ⚠️ Con AST y no con texto: la primera versión partía por línea y el `return`
    tiene dos claves en el mismo renglón, así que se comió `volvio` — un parser
    que ve de menos y no falla es el mismo bug que este archivo persigue."""
    import ast
    import textwrap

    arbol = ast.parse(textwrap.dedent(inspect.getsource(cen._lo_de_hoy)))
    for n in ast.walk(arbol):
        if not (isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)):
            continue
        return {k.value for k, v in zip(n.value.keys, n.value.values, strict=True)
                if isinstance(k, ast.Constant) and isinstance(v, ast.Call)
                and getattr(v.func, "id", "") == "_por_hora"}
    raise AssertionError("`_lo_de_hoy` dejó de devolver un dict literal")


def test_TODO_bloque_del_payload_tiene_rotulo():
    """Sin rótulo el bloque igual se imprime —con su clave cruda, feo y
    visible— pero eso es un recordatorio, no un diseño. El test lo convierte en
    algo que se arregla antes de mergear."""
    faltan = _bloques_del_payload() - set(d._ROTULOS)
    assert not faltan, (
        f"bloques nuevos de AHORA sin rótulo en el diag: {sorted(faltan)}. "
        f"Agregalos a `_ROTULOS` — el diag existe para que la consola no pueda "
        f"decir algo distinto de la pantalla.")


def test_el_diag_NO_lista_los_bloques_a_mano():
    """⚠️ La causa raíz. Si alguien vuelve a escribir la lista fija, el próximo
    bloque nuevo se pierde en silencio otra vez."""
    src = inspect.getsource(d.main)
    assert "listas = {" in src and "isinstance(v, list)" in src, (
        "el diag volvió a listar bloques a mano en vez de derivarlos del payload")


def test_los_rotulos_declarados_EXISTEN():
    """Un rótulo que nombra un bloque borrado es una lista que se pudre — el
    mismo criterio que el trinquete de `test_capas`."""
    fantasmas = set(d._ROTULOS) - _bloques_del_payload()
    assert not fantasmas, (
        f"`_ROTULOS` nombra bloques que el payload ya no manda: "
        f"{sorted(fantasmas)}")


def test_lo_SIN_CONFIRMAR_no_cuenta_como_nada_paso():
    """El veredicto «hoy no pasó nada» no puede salir con hallazgos que nadie
    verificó: ahí no pasó nada **que sepamos**, que es lo contrario."""
    src = inspect.getsource(d.main)
    i = src.index("nada = (")
    assert "not sc" in src[i:i + 300], (
        "el veredicto «hoy no pasó nada» ignora lo que no se pudo confirmar")
