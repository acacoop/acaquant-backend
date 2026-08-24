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


def test_la_clave_se_arma_EN_UN_SOLO_LUGAR():
    """El control, la acción, el detector y el backfill piden la clave a la
    MISMA función. Si cada uno la armara, moverían objetos distintos y el
    hallazgo seguiría igual **sin dar ningún error** — el síntoma exacto que
    esto vino a arreglar (REGLA #9)."""
    from api.services import av_agent_registro as registro
    from api.services.av_agent_hacer import _mover_item
    from jobs.controles_datos import _espejar_items

    for fn in (_mover_item, _espejar_items):
        assert "clave" in codigo(fn) or "sincronizar" in codigo(fn)
    assert "clave_de_problema" in codigo(_mover_item)
    # ⚠️ `persistir` ya no la arma: la arma LA PUERTA (`av_agent_registro`), que
    # desde la Fase 1 es el único lugar que escribe lo que el agente encontró.
    # Un test aparte (`test_av_agent_puerta`) prohíbe armarla en cualquier otro
    # lado, incluido SQL.
    assert "clave_de_problema" in codigo(registro._fila)


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


# ── UN problema, UN objeto ──────────────────────────────────────────────────
#
# Ayer el mismo bono roto producía DOS objetos, porque el tipo y el origen
# entraban en la clave:
#
#     detector →  precio_moneda|live|bpoa7|pata_equivocada
#     control  →  control|control:patas_equivocadas|bpoa7|patas_equivocadas
#
# Hubo que construir un puente para moverlos juntos. Hoy el puente **sobra**:
# la identidad es (SUJETO, CAUSA) y quién lo vio es un atributo (§0.bj).


def test_el_detector_y_el_control_escriben_EL_MISMO_objeto():
    """El test que ayer probaba que eran distintos, hoy prueba que son uno.
    Ése era el objetivo — el puente era el parche, no la solución."""
    from api.services.av_agent_items import clave_de_problema
    det = clave_de_problema("BPOA7", "pata_equivocada", "live")
    ctl = clave_de_problema("BPOA7", "patas_equivocadas", "control:patas_equivocadas")
    assert det == ctl == "bpoa7|pata_equivocada"


def test_el_TIPO_y_el_ORIGEN_no_son_identidad():
    """Son QUIÉN LO VIO. Que dos cosas miren el mismo problema no lo convierte
    en dos problemas."""
    from api.services.av_agent_items import clave_de_problema
    a = clave_de_problema("AL30", "sin_punta", "live")
    b = clave_de_problema("AL30", "sin_punta", "soberanos")
    c = clave_de_problema("AL30", "sin_punta", "")
    assert a == b == c


def test_problemas_DISTINTOS_del_mismo_bono_NO_se_fusionan():
    """La otra mitad: si la causa cambia, es otro problema y otra historia."""
    from api.services.av_agent_items import clave_de_problema
    assert (clave_de_problema("AL30", "sin_punta")
            != clave_de_problema("AL30", "pata_equivocada"))


def test_lo_que_NO_tiene_sujeto_no_colapsa_en_uno_solo():
    """Un `db_cambio` habla de la base entera. Sin respaldo, todos los
    sin-sujeto de una misma causa serían UN objeto y se taparían entre ellos."""
    from api.services.av_agent_items import clave_de_problema
    a = clave_de_problema("", "sin_escribir", "sistema")
    b = clave_de_problema("", "sin_escribir", "live")
    assert a and b and a != b


def test_la_equivalencia_de_causas_se_DERIVA_de_las_acciones():
    """`patas_equivocadas` → `pata_equivocada` sale de `ACCIONES`, donde cada
    acción ya declara sus dos puntas (`sobre` y `causa`). Una tabla aparte diría
    una cosa y el eval set otra, sin fallar nunca."""
    from api.services.av_agent_items import _equivalencias, causa_canonica
    eq = _equivalencias()
    assert eq.get("patas_equivocadas") == "pata_equivocada"
    assert causa_canonica("patas_equivocadas") == "pata_equivocada"
    # Una causa que no tiene acción se queda como está: no se inventa nada.
    assert causa_canonica("sin_ejes") == "sin_ejes"


def test_el_PUENTE_se_borro():
    """Un puente a ninguna parte que sigue exportado es una función que alguien
    va a usar creyendo que hace falta — y ahí vuelven los dos objetos por otro
    camino (REGLA #5)."""
    from api.services import av_agent_items
    assert not hasattr(av_agent_items, "marcar_por")


def test_la_accion_mueve_UN_solo_objeto():
    from api.services.av_agent_hacer import _mover_item
    src = codigo(_mover_item)
    assert "clave_de_problema(p.sujeto, _causa_de(a), a.sobre)" in src
    assert "marcar_por" not in src


def test_sigue_pasando_a_EN_CURSO_y_no_a_RESUELTO():
    from api.services.av_agent_hacer import _mover_item
    src = codigo(_mover_item)
    assert "ciclo.EN_CURSO" in src and "RESUELTO" not in src
