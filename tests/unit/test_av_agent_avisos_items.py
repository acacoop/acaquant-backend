"""LO QUE EL AGENTE MANDA TAMBIÉN ES UN OBJETO (§0.bk).

Quinta migración. Hasta acá el modelo cubría lo que el agente **encuentra**
(hallazgos, controles, centinela) y no lo que **manda**: avisos, filas de aviso
y preguntas vivían con su propio `resuelto bool` / `hecho bool` /
`estado text`, sin memoria.

Y no son otra cosa. *«A este bono le falta el CER de emisión»* es
**(qué cosa, qué le pasa)**: la misma identidad que un hallazgo. Tanto que,
cuando el detector encuentra ese mismo dato faltando, los dos terminan
escribiendo en el MISMO objeto — que es exactamente lo correcto, porque es un
solo problema visto dos veces.
"""
from __future__ import annotations

from ._fuente import codigo

# ── los AVISOS ───────────────────────────────────────────────────────────────

def test_crear_un_aviso_crea_su_objeto():
    from api.services.av_agent_vista import crear_avisos
    assert "_espejar_aviso(" in codigo(crear_avisos)


def test_avisarle_a_una_persona_crea_su_objeto():
    from api.services.av_agent_vista import avisar_a
    assert "_espejar_aviso(" in codigo(avisar_a)


def test_cerrar_un_aviso_cierra_su_objeto():
    from api.services.av_agent_vista import resolver_aviso, resolver_aviso_propio
    assert "_cerrar_aviso(" in codigo(resolver_aviso)
    assert "_cerrar_aviso(" in codigo(resolver_aviso_propio)


def test_reabrir_es_VOLVIO_y_no_borra_la_vuelta():
    """El vocabulario solo deja salir de `resuelto` por `volvio`, y además es lo
    que pasó: alguien lo dio por hecho y el pendiente sigue. Marcarlo `nuevo`
    borraría esa vuelta, que es justo lo que el seguimiento tiene que contar."""
    from api.services.av_agent_vista import _cerrar_aviso
    src = codigo(_cerrar_aviso)
    assert "ciclo.VOLVIO if reabrir else ciclo.RESUELTO" in src


def test_el_espejo_NUNCA_puede_tumbar_la_lista_de_pendientes():
    """Es memoria, no es la lista. Si el objeto no se puede escribir, el aviso
    tiene que existir igual — al revés sería cambiar una funcionalidad que anda
    por una que estamos estrenando."""
    from api.services.av_agent_vista import _cerrar_aviso, _espejar_aviso
    for f in (_espejar_aviso, _cerrar_aviso):
        assert "except Exception" in codigo(f)


def test_el_mensaje_sin_ticker_no_colapsa_con_los_demas():
    """Sin sujeto, todos los mensajes de un mismo tema serían UN objeto y se
    taparían entre ellos. El destinatario hace de sujeto."""
    from api.services.av_agent_vista import _sujeto_aviso
    assert _sujeto_aviso("AL30", "x@y.com") == "AL30"
    assert _sujeto_aviso("", "X@Y.com") == "x@y.com"
    assert _sujeto_aviso("", "") == ""


def test_el_aviso_y_el_hallazgo_del_MISMO_dato_son_UN_objeto():
    """La prueba de que la identidad es (sujeto, causa) y no quién lo vio: el
    alta deja el aviso `cer_emision` sobre AL30 y el detector nocturno emite la
    misma causa sobre el mismo bono. **Un problema, un objeto.**"""
    from api.services.av_agent_items import clave_de_problema
    assert (clave_de_problema("AL30", "cer_emision", "aviso")
            == clave_de_problema("AL30", "cer_emision", "soberanos"))


# ── las FILAS de un aviso ────────────────────────────────────────────────────

def test_cada_fila_del_mensaje_es_su_propio_objeto():
    """Espejar el aviso entero no alcanza: se rearma en cada corrida, así que
    nunca podría decir «esta cuenta lleva CUATRO DÍAS descubierta»."""
    from api.services.av_agent_mensajes import enviar_tabla
    assert "_espejar_filas(" in codigo(enviar_tabla)


def test_tildar_una_fila_cierra_su_objeto_y_destildar_lo_devuelve():
    from api.services.av_agent_mensajes import _cerrar_fila, marcar_item
    assert "_cerrar_fila(" in codigo(marcar_item)
    assert "ciclo.RESUELTO if hecho else ciclo.VOLVIO" in codigo(_cerrar_fila)


def test_el_espejo_de_las_filas_tampoco_puede_tumbar_el_envio():
    from api.services.av_agent_mensajes import _cerrar_fila, _espejar_filas
    for f in (_espejar_filas, _cerrar_fila):
        assert "except Exception" in codigo(f)


# ── las PREGUNTAS ────────────────────────────────────────────────────────────

def test_registrar_una_pregunta_crea_su_objeto():
    from api.services.av_agent_preguntas import registrar
    assert "_espejar_pregunta(" in codigo(registrar)


def test_responder_cierra_su_objeto():
    from api.services.av_agent_preguntas import responder
    assert "_cerrar_pregunta(" in codigo(responder)


def test_la_identidad_NO_sale_de_partir_la_clave():
    """⚠️ La clave es `falta:AL30` y separarla con un `split(':')` sería trivial
    — y ataría la identidad a una convención de texto (REGLA #9). El día que un
    sujeto traiga `:` adentro partiría mal, en silencio."""
    from api.services import av_agent_preguntas as p
    src = codigo(p._espejar_pregunta) + codigo(p._cerrar_pregunta)
    assert "split" not in src
    assert 'q.get("sujeto")' in src and 'q.get("causa")' in src


def test_una_pregunta_sin_identidad_NO_se_espeja():
    """Prefiero que le falte la memoria a que la tenga mal: un objeto con el
    sujeto equivocado es peor que no tenerlo, porque se lee como cierto."""
    from api.services.av_agent_preguntas import _cerrar_pregunta, _espejar_pregunta
    # No toca la base: sale antes de importar el store.
    _espejar_pregunta({"clave": "x", "pregunta": "?"})
    _cerrar_pregunta({"clave": "x"})


def test_las_preguntas_que_se_generan_declaran_sujeto_y_causa():
    """Si el constructor no los pone, el espejo se saltea en silencio y la
    migración queda escrita pero apagada."""
    from api.services.av_agent_preguntas import preguntas_de_hallazgos
    qs = preguntas_de_hallazgos([
        {"tipo": "falta_en_base", "ticker": "TZXD8", "evidencia": {}},
        {"tipo": "sin_curva", "ticker": "AL30",
         "evidencia": {"ajuste": "badlar", "tickers": ["AL30"]}},
    ])
    assert qs, "el fixture dejó de generar preguntas"
    for q in qs:
        assert q.get("sujeto") and q.get("causa"), q["clave"]


def test_la_identidad_se_PERSISTE_o_responder_no_puede_cerrar_nada():
    """`responder()` lee la fila de la base, no el dict original. Si `sujeto` y
    `causa` no fueran columnas, cerraría el objeto de nadie."""
    from api.services import av_agent_preguntas as p
    assert "sujeto" in p._COLS and "causa" in p._COLS
    assert "sujeto, causa" in codigo(p.registrar)


# ── el avance, contado sin mentir ────────────────────────────────────────────

def test_las_cinco_que_espejan_estan_DECLARADAS_y_no_adivinadas():
    """El conteo sale de un campo (`Forma.espeja`) y no de buscar la palabra
    «espeja» adentro del texto: una barra de progreso que se calcula leyendo un
    comentario miente el día que alguien reescribe el comentario."""
    from core import ciclo
    assert set(ciclo.espejan()) == {
        "mercado.av_agent_centinela", "manager.controles_datos",
        "mercado.av_agent_avisos", "mercado.av_agent_aviso_items",
        "mercado.av_agent_preguntas",
    }
    assert all(t in ciclo.sin_migrar() for t in ciclo.espejan()), (
        "espejar NO es haber migrado: la columna vieja sigue ahí")
