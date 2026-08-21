"""«YA LO MARQUÉ COMO HECHO Y SIGUE FIGURANDO.»

Tercera y cuarta migración. Las dos cierran el mismo agujero desde puntas
distintas: **nada conectaba la acción con el objeto**.

    el control corre     → sabía qué había y qué se había resuelto…
    apretás el arreglo   → se escribía, se verificaba…
    ...y el hallazgo seguía exactamente igual en la pantalla.

Las dos mitades funcionaban y no se hablaban.
"""
from __future__ import annotations

from ._fuente import codigo

# ── los CONTROLES, como objetos ─────────────────────────────────────────────

def test_el_control_espeja_sus_anomalias():
    from jobs.controles_datos import _diff_y_persistir
    assert "_espejar_items(control_id" in codigo(_diff_y_persistir)


def test_el_espejo_no_puede_tumbar_el_control():
    from jobs.controles_datos import _diff_y_persistir
    cola = codigo(_diff_y_persistir).split("_espejar_items(control_id")[1][:300]
    assert "except Exception" in cola


def test_cada_control_concilia_SU_universo():
    """Un `origen` por control: si compartieran uno, correr `assets_sin_cartera`
    cerraría las anomalías de `contrapartes_pendientes` por no haberlas visto."""
    from jobs.controles_datos import _espejar_items
    assert 'f"control:{control_id}"' in codigo(_espejar_items)


def test_acá_SI_se_puede_cerrar_por_ausencia():
    """Es el caso limpio y conviene que quede escrito por qué: a
    `_diff_y_persistir` **solo se llega si el control no levantó** (en `main()`
    está adentro del try). Una lista vacía significa de verdad «no hay
    anomalías» y no «no pude mirar» — la distinción que costó §0.be y §0.bf."""
    from jobs.controles_datos import _espejar_items
    assert 'evaluados={"control"}' in codigo(_espejar_items)


def test_el_control_que_EXPLOTA_no_llega_a_persistir():
    """La premisa del test de arriba, verificada en el código y no asumida."""
    import jobs.controles_datos as m
    src = codigo(m.main)
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
    src = codigo(_espejar_items)
    assert "av_agent_salud" in src and '"rompe"' in src


# ── APLICAR un arreglo mueve el objeto ──────────────────────────────────────

def test_aplicar_mueve_el_item():
    from api.services.av_agent_hacer import _aplicar_una
    assert "_mover_item(a, p)" in codigo(_aplicar_una)


def test_pasa_a_EN_CURSO_y_no_a_RESUELTO():
    """⚠️ La distinción que hace honesto al sistema: **escribir el dato no es lo
    mismo que el problema haya desaparecido.** Quien lo declara resuelto es el
    DETECTOR, cuando vuelve a mirar y ya no lo encuentra — y recién ahí arrancan
    los hitos. Si lo cerrara la propia acción, el agente estaría calificando su
    propio trabajo."""
    from api.services.av_agent_hacer import _mover_item
    src = codigo(_mover_item)
    assert "ciclo.EN_CURSO" in src and "RESUELTO" not in src


def test_la_clave_se_arma_IGUAL_que_en_el_control():
    """Si se calculara distinto, movería un objeto que no existe y el hallazgo
    seguiría igual **sin dar ningún error** — el síntoma exacto que esto viene
    a arreglar."""
    from api.services.av_agent_hacer import _mover_item
    from core import ciclo
    from jobs.controles_datos import _espejar_items

    # Lo que escribe el control…
    vistos = codigo(_espejar_items)
    assert '"tipo": "control"' in vistos and '"regla": control_id' in vistos
    # …y lo que busca la acción: mismas cuatro partes, mismo orden.
    assert 'ciclo.clave_de("control", f"control:{a.sobre}", p.sujeto, a.sobre)' \
        in codigo(_mover_item)
    # Y la fórmula da lo mismo desde los dos lados.
    del_control = ciclo.clave_de("control", "control:assets_sin_cartera",
                                 "[42932] PBJ26", "assets_sin_cartera")
    de_la_accion = ciclo.clave_de("control", "control:assets_sin_cartera",
                                  "[42932] PBJ26", "assets_sin_cartera")
    assert del_control == de_la_accion


def test_mover_el_item_no_puede_tumbar_la_accion():
    """La escritura real ya pasó y no se deshace por un problema de estado."""
    from api.services.av_agent_hacer import _aplicar_una
    cola = codigo(_aplicar_una).split("_mover_item(a, p)")[1][:300]
    assert "except Exception" in cola


def test_el_ciclo_PERMITE_ese_salto():
    """Un `nuevo → en_curso` que el vocabulario rechazara dejaría la acción
    aplicada y el objeto quieto."""
    from core import ciclo
    assert ciclo.puede_pasar(ciclo.NUEVO, ciclo.EN_CURSO)
    assert ciclo.puede_pasar(ciclo.VISTO, ciclo.EN_CURSO)
    assert ciclo.puede_pasar(ciclo.EN_CURSO, ciclo.RESUELTO)


# ── UN problema, DOS objetos: el puente ─────────────────────────────────────

def test_el_mismo_problema_lo_ven_DOS_caminos():
    """Medido antes de tocar nada, y por eso hubo que construir el puente:

        detector →  precio_moneda|live|bpoa7|pata_equivocada
        control  →  control|control:patas_equivocadas|bpoa7|patas_equivocadas

    Un bono con la pata mal cargada, DOS objetos, porque lo miran dos cosas
    distintas. Si la acción moviera solo uno, el hallazgo seguiría figurando
    **igual que antes de toda la migración** — el mismo síntoma, adentro del
    modelo nuevo."""
    from core import ciclo
    det = ciclo.clave_de("precio_moneda", "live", "BPOA7", "pata_equivocada")
    ctl = ciclo.clave_de("control", "control:patas_equivocadas", "BPOA7",
                         "patas_equivocadas")
    assert det != ctl


def test_la_accion_mueve_LOS_DOS():
    from api.services.av_agent_hacer import _mover_item
    src = codigo(_mover_item)
    assert "av_agent_items.marcar(clave" in src      # el del control
    assert "marcar_por(sujeto=" in src               # el del detector


def test_se_juntan_por_SUJETO_y_CAUSA_que_es_lo_unico_que_comparten():
    """La causa es la misma de los dos lados: `Accion.causa` ES la regla del
    detector, declarada para el eval set (§0.av). Sin eso haría falta una tabla
    de equivalencias, que es otra cosa que se desincroniza sola."""
    from api.services.av_agent_hacer import ACCIONES, _causa_de
    a = ACCIONES["mercado.apuntar_pata"]
    assert _causa_de(a) == "pata_equivocada"       # lo que emite el detector
    assert a.sobre == "patas_equivocadas"          # lo que emite el control
    assert _causa_de(a) != a.sobre, "si fueran iguales, el puente sobraría"


def test_marcar_por_NO_hace_saltos_imposibles():
    """Mover un `resuelto` a `en_curso` borraría su seguimiento: el vocabulario
    lo rechaza y `marcar_por` respeta esa lista en vez de actualizar a ciegas."""
    from api.services.av_agent_items import marcar_por
    src = codigo(marcar_por)
    assert "puede_pasar(e, estado)" in src and "estado = ANY(%s)" in src


def test_marcar_por_exige_las_DOS_claves():
    """Mover «todo lo de este sujeto» sin la causa tocaría hallazgos de otros
    problemas del mismo bono."""
    from api.services.av_agent_items import marcar_por
    from core import ciclo
    assert marcar_por(sujeto="AL30", regla="", estado=ciclo.EN_CURSO)["ok"] is False
    assert marcar_por(sujeto="", regla="x", estado=ciclo.EN_CURSO)["ok"] is False
