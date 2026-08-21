"""UNA PASADA QUE EXPLOTA NO PUEDE DECIR «SE ARREGLÓ SOLO».

`_observar()` mira tres cosas —precios, tasas y salud— **cada una en su propio
`try`**, para que la caída de una no deje al centinela sin mirar las otras. Eso
está bien y no se toca.

El problema era lo que venía después. Los tres `try` se tragan la excepción y
devuelven una lista más corta, así que el que llama **no podía distinguir «no
encontró nada» de «explotó»**. Y el auto-resuelto decía:

    if hallazgos:      # ← «si algo trajo, cerrá todo lo demás»
        UPDATE ... SET resuelto_como = 'solo' WHERE ultimo_at < marca

El comentario de arriba decía *«solo cuando la pasada fue COMPLETA»* y **eso no
era lo que el código chequeaba**: con el bloque de tasas caído, los precios
igual traían algo, la condición pasaba, y **todos los `tasa_sospechosa` se
marcaban como arreglados solos**. Silencioso y del lado optimista.

Es el mismo modo de falla que `evaluados` tapó en el censo (§0.be).
"""
from __future__ import annotations

import inspect

from api.services import av_agent_centinela as c


def _codigo(fn) -> str:
    """El código SIN comentarios.

    ⚠️ Es la tercera vez esta semana que un test de este tipo se caza a sí mismo
    con el comentario que explica el bug (el comentario nombra `if hallazgos:`
    justo para decir que ya no está). Un test que lee el fuente tiene que leer
    lo que se EJECUTA."""
    return "\n".join(l.split("#")[0].rstrip()
                      for l in inspect.getsource(fn).splitlines())


# ── _observar declara QUÉ ALCANZÓ A MIRAR ───────────────────────────────────

def test_observar_devuelve_tambien_lo_que_evaluo():
    assert "tuple[list[dict], set[str]]" in inspect.getsource(c._observar).split("\n")[0]


def test_cada_pasada_declara_sus_TIPOS():
    """Se declara y no se deduce de lo que devolvió: una pasada que no encontró
    nada y una que explotó devuelven lo mismo — nada."""
    assert set(c._CUBRE) == {"precios", "tasas", "salud"}
    for bloque, tipos in c._CUBRE.items():
        assert tipos, bloque


def test_los_tipos_declarados_EXISTEN():
    """Un tipo inventado acá cerraría hallazgos que nadie emite, o —peor— no
    cerraría los que sí."""
    from api.services.av_agent import ACCION_POR_TIPO
    for tipos in c._CUBRE.values():
        for t in tipos:
            assert t in ACCION_POR_TIPO, f"«{t}» no lo emite ningún detector"


def test_cada_bloque_marca_lo_suyo_DESPUES_de_correr():
    """`evaluados.update()` va DENTRO del `try` y después de la llamada: si
    fuera antes, una excepción dejaría el tipo marcado como evaluado."""
    src = inspect.getsource(c._observar)
    for bloque in ("precios", "tasas", "salud"):
        i = src.index(f'_CUBRE["{bloque}"]')
        # entre el update y el `except` de su bloque no puede haber otro `try`
        assert "except Exception" in src[i:i + 300], bloque


def test_una_pasada_CAIDA_no_marca_su_tipo(monkeypatch):
    """El caso real: si el bloque de tasas explota, `tasa_sospechosa` NO puede
    quedar en `evaluados` — si no, se cierran todos."""
    from api.services import av_agent
    monkeypatch.setattr(av_agent, "relevar_live", lambda: {"hallazgos": []})
    monkeypatch.setattr(av_agent, "detectar_tasas_sospechosas",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(av_agent, "detectar_salud", lambda *a: [])
    monkeypatch.setattr("api.services.salud.evaluar", lambda: [])
    _h, evaluados = c._observar()
    assert "tasa_sospechosa" not in evaluados
    assert "sin_precio" in evaluados, "la pasada que SÍ corrió tiene que contar"


def test_si_se_cae_TODO_no_se_evalua_nada(monkeypatch):
    """Y con `evaluados` vacío no se cierra una sola fila."""
    from api.services import av_agent
    boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))  # noqa: E731
    monkeypatch.setattr(av_agent, "relevar_live", boom)
    monkeypatch.setattr(av_agent, "detectar_tasas_sospechosas", boom)
    monkeypatch.setattr("api.services.salud.evaluar", boom)
    _h, evaluados = c._observar()
    assert evaluados == set()


# ── el auto-resuelto usa esa declaración, no «si trajo algo» ────────────────

def test_el_auto_resuelto_se_limita_a_los_tipos_EVALUADOS():
    src = _codigo(c.ciclo)
    assert "if evaluados:" in src, "volvió a cerrar por «si trajo algo»"
    assert "AND tipo = ANY(%s)" in src
    assert "if hallazgos:" not in src


def test_el_espejo_en_items_usa_LA_MISMA_guarda():
    """Si el centinela y su espejo cerraran con criterios distintos, AHORA y
    ENCONTRÓ volverían a contar historias diferentes del mismo problema — que
    es justo lo que la migración vino a terminar."""
    src = _codigo(c.ciclo)
    assert "sincronizar(\"live\"" in src and "evaluados=evaluados" in src


def test_el_espejo_no_puede_tumbar_la_pasada():
    """La pasada del centinela es lo que la mesa mira en rueda."""
    cola = _codigo(c.ciclo).split("av_agent_items.sincronizar")[1][:400]
    assert "except Exception" in cola
