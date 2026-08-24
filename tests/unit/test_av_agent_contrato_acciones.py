"""EL CONTRATO DE LAS ACCIONES — que ninguna pueda MENTIR que verificó.

⚠️⚠️ **ESTE ES EL MODO DE FALLA MÁS CARO QUE PUEDE TENER EL AGENTE, Y NO ROMPE
NADA.**

Las diez acciones cumplen el mismo contrato (`proponer` · `aplicar` ·
`verificar`) y por eso el orquestador las trata igual: llama `aplicar()` y
después `verificar()`, sin saber cuál está corriendo. Eso es lo que hizo que la
parada de emergencia cubriera las diez con una línea.

Pero ese mismo desacople abre un agujero: **si una acción nueva implementara
`verificar()` devolviendo `True, "ok"` sin releer nada, todo compilaría, todos
los tests pasarían, y nada fallaría jamás.** El agente empezaría a anotar en el
eval set arreglos que nunca se comprobaron — y el eval set es literalmente la
compuerta que habilita cada paso de autonomía. Un voto falso ahí no se nota
mirando la pantalla: se nota el día que el agente hace algo solo porque «venía
acertando».

Lo que hoy protege eso es una costumbre: las diez releen. `AccionCartera` hasta
lo dice en su docstring — *«no se confía en que el UPDATE salió bien»*. Una
costumbre no sobrevive al apuro; un test que rompe el build, sí.

Estos tests corren contra **TODAS** las acciones a la vez, no contra cada una
por separado, que es la única forma de que la número once quede cubierta el día
que se escriba.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from api.services.av_agent_hacer import ACCIONES

_IDS = sorted(ACCIONES)


def _arbol(fn) -> ast.FunctionDef:
    return ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]


@pytest.mark.parametrize("aid", _IDS)
def test_verificar_LEE_algo(aid: str):
    """No alcanza con devolver un booleano: hay que haber ido a buscarlo.

    Se exige al menos una llamada en el cuerpo. Un `verificar` que solo arma su
    respuesta con lo que ya tenía en la propuesta no verificó: repitió.
    """
    fn = _arbol(type(ACCIONES[aid]).verificar)
    llamadas = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    # Las de formateo no cuentan: `str(...)`, `f"...".strip()` y compañía no
    # traen un dato de ningún lado.
    _FORMATO = {"str", "int", "float", "strip", "lower", "upper", "format",
                "join", "replace", "round", "len", "bool"}
    reales = [c for c in llamadas
              if (getattr(c.func, "id", None) or getattr(c.func, "attr", ""))
              not in _FORMATO]
    assert reales, (
        f"«{aid}».verificar() no llama a nada: está afirmando que verificó sin "
        f"ir a buscar el estado. Ese voto entra al eval set como si fuera real.")


@pytest.mark.parametrize("aid", _IDS)
def test_verificar_NO_devuelve_True_sin_condicion(aid: str):
    """El caso exacto que este archivo existe para prohibir: `return True, "ok"`.

    Un `return True` es legítimo **solo si hay algo que pueda devolver False** —
    o sea, si el cuerpo tiene una decisión adentro. Si el único camino de salida
    es afirmativo, la acción no puede fallar nunca, y eso no es una verificación:
    es un sello de goma.
    """
    fn = _arbol(type(ACCIONES[aid]).verificar)
    decide = any(isinstance(n, (ast.If, ast.Compare, ast.IfExp, ast.BoolOp))
                 for n in ast.walk(fn))
    siempre_si = all(
        isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2
        and isinstance(r.value.elts[0], ast.Constant)
        and r.value.elts[0].value is True
        for r in [n for n in ast.walk(fn)
                  if isinstance(n, ast.Return) and n.value is not None])
    assert decide or not siempre_si, (
        f"«{aid}».verificar() devuelve True por el único camino que tiene: "
        f"no puede contestar que NO. Es un sello de goma, no una verificación.")


@pytest.mark.parametrize("aid", _IDS)
def test_verificar_devuelve_el_par_del_contrato(aid: str):
    """`(salio_bien, por_qué)`. El motivo no es decorativo: es lo que se muestra
    cuando algo no salió, y una acción que devuelve solo el booleano deja al que
    mira sin saber qué pasó."""
    fn = _arbol(type(ACCIONES[aid]).verificar)
    for r in [n for n in ast.walk(fn) if isinstance(n, ast.Return)]:
        if r.value is None:
            pytest.fail(f"«{aid}».verificar() tiene un `return` pelado")
        assert isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2, (
            f"«{aid}».verificar() devuelve algo que no es (bool, motivo)")


def test_las_TRES_del_contrato_estan_en_las_DIEZ():
    """Si una acción no implementa alguna, hereda la del Protocol —que es `...`,
    o sea `None`— y el orquestador la trata como si hubiera contestado."""
    for aid, a in ACCIONES.items():
        for metodo in ("proponer", "aplicar", "verificar"):
            fn = getattr(type(a), metodo, None)
            assert callable(fn), f"«{aid}» no implementa {metodo}()"
            cuerpo = _arbol(fn).body
            solo_puntos = (len(cuerpo) == 1 and isinstance(cuerpo[0], ast.Expr)
                           and isinstance(cuerpo[0].value, ast.Constant)
                           and cuerpo[0].value.value is Ellipsis)
            assert not solo_puntos, (
                f"«{aid}».{metodo}() quedó en `...`: devuelve None y el "
                f"orquestador lo lee como una respuesta")


def test_este_test_cubre_a_TODAS_y_no_a_una_lista():
    """⚠️ La razón de ser del archivo. Si mañana alguien suma la acción once y
    estos tests estuvieran escritos contra una lista fija, la nueva nacería sin
    contrato — que es exactamente el agujero que vinimos a tapar."""
    assert set(_IDS) == set(ACCIONES), "la parametrización se desincronizó"
    assert len(_IDS) >= 10, f"hay {len(_IDS)} acciones y se esperaban 10 o más"
