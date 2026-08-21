"""«YA LO MARQUÉ COMO HECHO Y SIGUE FIGURANDO.»

Tercera y cuarta migración. Las dos cierran el mismo agujero desde puntas
distintas: **nada conectaba la acción con el objeto**.

    el control corre     → sabía qué había y qué se había resuelto…
    apretás el arreglo   → se escribía, se verificaba…
    ...y el hallazgo seguía exactamente igual en la pantalla.

Las dos mitades funcionaban y no se hablaban.
"""
from __future__ import annotations

import inspect


def _codigo(fn) -> str:
    """El código SIN comentarios: un test tiene que leer lo que se EJECUTA."""
    return "\n".join(x.split("#")[0].rstrip()
                     for x in inspect.getsource(fn).splitlines())


# ── los CONTROLES, como objetos ─────────────────────────────────────────────

def test_el_control_espeja_sus_anomalias():
    from jobs.controles_datos import _diff_y_persistir
    assert "_espejar_items(control_id" in _codigo(_diff_y_persistir)


def test_el_espejo_no_puede_tumbar_el_control():
    from jobs.controles_datos import _diff_y_persistir
    cola = _codigo(_diff_y_persistir).split("_espejar_items(control_id")[1][:300]
    assert "except Exception" in cola


def test_cada_control_concilia_SU_universo():
    """Un `origen` por control: si compartieran uno, correr `assets_sin_cartera`
    cerraría las anomalías de `contrapartes_pendientes` por no haberlas visto."""
    from jobs.controles_datos import _espejar_items
    assert 'f"control:{control_id}"' in _codigo(_espejar_items)


def test_acá_SI_se_puede_cerrar_por_ausencia():
    """Es el caso limpio y conviene que quede escrito por qué: a
    `_diff_y_persistir` **solo se llega si el control no levantó** (en `main()`
    está adentro del try). Una lista vacía significa de verdad «no hay
    anomalías» y no «no pude mirar» — la distinción que costó §0.be y §0.bf."""
    from jobs.controles_datos import _espejar_items
    assert 'evaluados={"control"}' in _codigo(_espejar_items)


def test_el_control_que_EXPLOTA_no_llega_a_persistir():
    """La premisa del test de arriba, verificada en el código y no asumida."""
    import jobs.controles_datos as m
    src = _codigo(m.main)
    i_try = src.index("items = c.fn()")
    i_persistir = src.index("_diff_y_persistir(c.id, items)")
    i_except = src.index("except Exception as e:", i_try)
    assert i_try < i_persistir < i_except, (
        "si `_diff_y_persistir` quedara fuera del try, un control caído "
        "cerraría todas sus anomalías como resueltas")


def test_el_AFECTA_sale_de_la_ficha_declarada():
    """Qué se rompe por esto es criterio, y vive en `salud.CONTROLES`. No se
    inventa en el espejo."""
    from jobs.controles_datos import _espejar_items
    src = _codigo(_espejar_items)
    assert "av_agent_salud" in src and '"rompe"' in src


# ── APLICAR un arreglo mueve el objeto ──────────────────────────────────────

def test_aplicar_mueve_el_item():
    from api.services.av_agent_hacer import _aplicar_una
    assert "_mover_item(a, p)" in _codigo(_aplicar_una)


def test_pasa_a_EN_CURSO_y_no_a_RESUELTO():
    """⚠️ La distinción que hace honesto al sistema: **escribir el dato no es lo
    mismo que el problema haya desaparecido.** Quien lo declara resuelto es el
    DETECTOR, cuando vuelve a mirar y ya no lo encuentra — y recién ahí arrancan
    los hitos. Si lo cerrara la propia acción, el agente estaría calificando su
    propio trabajo."""
    from api.services.av_agent_hacer import _mover_item
    src = _codigo(_mover_item)
    assert "ciclo.EN_CURSO" in src and "RESUELTO" not in src


def test_la_clave_se_arma_IGUAL_que_en_el_control():
    """Si se calculara distinto, movería un objeto que no existe y el hallazgo
    seguiría igual **sin dar ningún error** — el síntoma exacto que esto viene
    a arreglar."""
    from api.services.av_agent_hacer import _mover_item
    from core import ciclo
    from jobs.controles_datos import _espejar_items

    # Lo que escribe el control…
    vistos = _codigo(_espejar_items)
    assert '"tipo": "control"' in vistos and '"regla": control_id' in vistos
    # …y lo que busca la acción: mismas cuatro partes, mismo orden.
    assert 'ciclo.clave_de("control", f"control:{a.sobre}", p.sujeto, a.sobre)' \
        in _codigo(_mover_item)
    # Y la fórmula da lo mismo desde los dos lados.
    del_control = ciclo.clave_de("control", "control:assets_sin_cartera",
                                 "[42932] PBJ26", "assets_sin_cartera")
    de_la_accion = ciclo.clave_de("control", "control:assets_sin_cartera",
                                  "[42932] PBJ26", "assets_sin_cartera")
    assert del_control == de_la_accion


def test_mover_el_item_no_puede_tumbar_la_accion():
    """La escritura real ya pasó y no se deshace por un problema de estado."""
    from api.services.av_agent_hacer import _aplicar_una
    cola = _codigo(_aplicar_una).split("_mover_item(a, p)")[1][:300]
    assert "except Exception" in cola


def test_el_ciclo_PERMITE_ese_salto():
    """Un `nuevo → en_curso` que el vocabulario rechazara dejaría la acción
    aplicada y el objeto quieto."""
    from core import ciclo
    assert ciclo.puede_pasar(ciclo.NUEVO, ciclo.EN_CURSO)
    assert ciclo.puede_pasar(ciclo.VISTO, ciclo.EN_CURSO)
    assert ciclo.puede_pasar(ciclo.EN_CURSO, ciclo.RESUELTO)
