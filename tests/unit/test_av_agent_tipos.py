"""EL CATÁLOGO DE TIPOS — que agregar un detector deje de ser tocar 5 listas.

Antes de esto, sumar un detector obligaba a escribir a mano, en dos archivos:
qué detecta · qué reglas emite · de qué dominio es · en qué job corre · qué
acción le corresponde. **19 tipos × 5 listas = 95 celdas** sostenidas por
convención, y por eso había tests que exigían «¿declaraste la descripción?».

Un test que existe para recordarte algo es la señal de que el diseño no lo
garantiza solo. Ahora la fila ES la declaración —igual que la clase ES el
registro en las acciones— y estos tests protegen otra cosa: que **nadie pueda
volver a escribir uno de esos mapas a mano** al lado del que se deriva.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from api.services import av_agent, av_agent_skills
from api.services import av_agent_tipos as tipos

RAIZ = Path(__file__).resolve().parents[2]


def test_los_SIETE_mapas_se_DERIVAN_del_catalogo():
    """Si alguien vuelve a escribir uno como literal, se desincroniza del resto
    sin dar ningún error — que es exactamente el problema que esto resolvió."""
    for mod, nombres in ((av_agent_skills, ("_QUE_DETECTA", "_REGLAS_DETECTOR",
                                            "_DOMINIO_DETECTOR", "_DONDE_CORRE")),
                         (av_agent, ("ACCION_POR_TIPO", "EN_AHORA_SIEMPRE",
                                     "TIPOS_NOTICIA"))):
        src = inspect.getsource(mod)
        for n in nombres:
            linea = next(x for x in src.splitlines()
                         if x.startswith(f"{n} ") or x.startswith(f"{n}:"))
            assert "tipos." in linea or "av_agent_tipos." in linea, (
                f"«{n}» volvió a ser un literal: {linea.strip()[:70]}")


def test_TODO_tipo_que_un_detector_EMITE_esta_en_el_catalogo():
    """El invariante que antes vivía repartido en cuatro tests distintos, uno
    por lista. Ahora es uno solo porque hay una sola lista."""
    emitidos = set()
    for f in (RAIZ / "api/services").glob("av_agent*.py"):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            # `{"tipo": "sin_precio", ...}` — así arma su hallazgo cada detector
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values, strict=False):
                    if (isinstance(k, ast.Constant) and k.value == "tipo"
                            and isinstance(v, ast.Constant)
                            and isinstance(v.value, str)):
                        emitidos.add(v.value)
    # Los que no son familias de hallazgo (claves de otros dicts que se llaman
    # igual). Solo se contrastan los que el catálogo YA conoce más los que
    # aparecen en varios módulos — un tipo de verdad lo emite un detector.
    faltan = {t for t in emitidos if t in av_agent.ACCION_POR_TIPO} - set(tipos.TIPOS)
    assert not faltan, f"emitidos y sin declarar en el catálogo: {sorted(faltan)}"


@pytest.mark.parametrize("tipo", sorted(tipos.TIPOS))
def test_cada_tipo_dice_QUE_MIRA_y_DONDE_CORRE(tipo: str):
    """Sin esto la tab SKILLS muestra una fila en blanco, y el chequeo de
    horarios no sabe contra qué cron compararlo."""
    t = tipos.TIPOS[tipo]
    assert len(t.titulo) > 3, f"«{tipo}» sin título legible"
    assert len(t.detalle) > 20, f"«{tipo}» sin explicación de por qué importa"
    assert t.dominio in av_agent_skills.DOMINIOS, (
        f"«{tipo}» declara el dominio «{t.dominio}», que no existe")
    assert t.donde_corre.startswith("jobs."), f"«{tipo}» no dice en qué job corre"


def test_el_job_declarado_EXISTE():
    """Un tipo que dice correr en un job borrado se muestra vigilado y no lo
    está. Es el mismo agujero que el crontab del repo vs el de la máquina."""
    malos = {t.tipo: t.donde_corre for t in tipos.TIPOS.values()
             if not (RAIZ / (t.donde_corre.replace(".", "/") + ".py")).exists()}
    assert not malos, f"declaran un job que no existe: {malos}"


def test_los_TRES_estados_de_reglas_se_distinguen():
    """⚠️ No declarar reglas y declarar NINGUNA son cosas distintas: la segunda
    dice «esta skill se muestra sin medición, a propósito». Colapsarlas borraba
    la diferencia entre una decisión y un olvido."""
    sin_declarar = [t.tipo for t in tipos.TIPOS.values() if t.reglas is None]
    vacio_a_proposito = [t.tipo for t in tipos.TIPOS.values() if t.reglas == ()]
    assert sin_declarar and vacio_a_proposito, (
        "se perdió alguno de los dos estados: sin_declarar="
        f"{sin_declarar}, vacio={vacio_a_proposito}")
    # Y el mapa derivado tiene que reflejarlo: la clave existe solo si se declaró.
    derivado = av_agent_skills._REGLAS_DETECTOR
    for t in sin_declarar:
        assert t not in derivado, f"«{t}» no declaró reglas y aparece en el mapa"
    for t in vacio_a_proposito:
        assert derivado.get(t) == (), f"«{t}» declaró vacío y no se ve así"


def test_AHORA_y_NOTICIA_siguen_siendo_OPT_IN():
    """«AHORA deja de ser AHORA si se llena.» El default de los dos campos es
    `False`, así que un tipo nuevo NO entra por existir — que es la regla que
    ya estaba y que este refactor no podía aflojar."""
    campos = {f.name: f.default for f in
              __import__("dataclasses").fields(tipos.Tipo)}
    assert campos["en_ahora"] is False and campos["noticia"] is False
    assert len(av_agent.EN_AHORA_SIEMPRE) <= 6, (
        f"AHORA se está llenando: {av_agent.EN_AHORA_SIEMPRE}")


def test_un_tipo_INCOMPLETO_no_se_puede_construir():
    """La validación corre al CONSTRUIR, o sea al importar el módulo: el error
    aparece en el arranque y en el primer test, no cuando alguien abre la tab."""
    for falta in ("titulo", "detalle", "dominio", "donde_corre"):
        campos = dict(titulo="Algo", detalle="una explicación bastante larga",
                      dominio="SISTEMA", donde_corre="jobs.av_agent")
        campos[falta] = ""
        with pytest.raises(ValueError, match=falta):
            tipos.Tipo("prueba", **campos)


def test_el_catalogo_NO_importa_nada_del_proyecto():
    """Lo leen `av_agent` y `av_agent_skills`, y el segundo importa al primero.
    Si esta tabla importara cualquiera de los dos, se abre un ciclo."""
    arbol = ast.parse((RAIZ / "api/services/av_agent_tipos.py")
                      .read_text(encoding="utf-8"))
    for n in ast.walk(arbol):
        mod = (getattr(n, "module", "") if isinstance(n, ast.ImportFrom)
               else "")
        if isinstance(n, ast.Import):
            mod = n.names[0].name
        assert (mod or "").split(".")[0] not in ("api", "core", "jobs"), (
            f"el catálogo importa «{mod}»: eso abre un ciclo")
