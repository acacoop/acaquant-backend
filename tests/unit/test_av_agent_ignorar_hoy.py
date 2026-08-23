"""IGNORAR ES UN SNOOZE DEL DÍA, NO UNA BLACKLIST (§0.cv).

El user (2026-08-22): *«si yo lo ignoro quiero que salga de ENCONTRAR — NO que
entre en una blacklist de cosas que nunca más me van a interesar. Si el agente
funciona bien y algo se rompe hoy, mañana lo va a volver a detectar, y TIENE
que volver a aparecer»*.

La primera versión escribía `agente.av_agent_ignorados` (permanente, por
ticker) y el user la usó semanas creyendo que descartaba EL AVISO del día. Lo
que congela este archivo es la separación de los DOS gestos:

    · IGNORAR de un hallazgo   → snooze del día (items), vence solo
    · «no nos interesa» de una pregunta de alta → durable (la tabla), con
      deshacer en DECIDIDO

Y el otro medio round (§0.cv-b): un diagnóstico que PRUEBA que el bono está
bien («viejo») CIERRA el hallazgo en el acto — *«tiene que figurar SOLAMENTE
lo que no está solucionado; para algo está el historial»*.
"""
from __future__ import annotations

import inspect


def codigo(fn) -> str:
    return inspect.getsource(fn)


# ── el botón IGNORAR ya no escribe la blacklist ─────────────────────────────

def test_ignorar_hallazgo_NO_escribe_la_tabla_durable():
    from api.services import av_agent_preguntas as preg
    src = codigo(preg.ignorar)
    assert "INSERT INTO agente.av_agent_ignorados" not in src
    # Lo que sí hace: mover los objetos a `ignorado` (de ahí sale de la vista).
    assert "_ignorar_objetos" in src


def test_la_respuesta_no_nos_interesa_SIGUE_siendo_durable():
    """El gesto de las preguntas de alta es OTRO juicio (sobre el papel, no
    sobre el aviso) y conserva su blacklist reversible."""
    from api.services import av_agent_preguntas as preg
    assert "INSERT INTO agente.av_agent_ignorados" in codigo(preg._aplicar_efecto)


def test_el_snooze_VENCE_solo_cuando_el_detector_lo_reve_otro_dia():
    """`ver()` reabre un item `ignorado` como `nuevo` (no `volvio`: nadie lo
    dio por arreglado) la primera vez que se lo re-ve en un día ART posterior."""
    from api.services import av_agent_items as items
    src = codigo(items.ver)
    assert "ultimo_at < %s" in src          # la frontera del día
    assert "_inicio_hoy_art()" in src       # calculada en ART, no UTC
    from core import ciclo
    # La transición existe en el modelo: ignorado → nuevo es legal.
    assert ciclo.puede_pasar(ciclo.IGNORADO, ciclo.NUEVO)


def test_la_fila_snoozeada_se_marca_y_no_cuenta_como_atendida():
    """La vista MARCA (`ignorado`) y no filtra — el corte lo hace la pantalla,
    que cuenta lo que esconde. Y un snooze no es «ya lo atendiste»: no puede
    aparecer en YA LO ATENDISTE mientras está escondido."""
    from api.services import av_agent_vista as v
    src = codigo(v.vista)
    assert 'h["ignorado"] = True' in src
    assert '"ignorados_hoy"' in src
    assert 'not h.get("ignorado") and (est == ciclo.VISTO' in src


# ── el desenlace «viejo» cierra solo ────────────────────────────────────────

def test_desenlace_viejo_gana_aunque_un_cotejo_quede_ambar():
    """El caso GD46: las ocho lentes dan bien (causa «sano») y el cotejo con
    1816 queda en ámbar por una diferencia de DEFINICIÓN de paridad. El
    veredicto tiene que ser «viejo», no «mirar» — decir «LEÉ LA TRABA» doce
    renglones arriba de «no se detecta nada roto» era la contradicción."""
    from api.services import av_agent_alta as alta
    chequeos = [
        {"clave": "cotejo", "titulo": "La PROPUESTA coincide con 1816",
         "estado": alta.REVISAR, "capa": alta.PRUEBA, "detalle": "paridad…"},
        {"clave": "causa_local", "titulo": "⇒ LA CONCLUSIÓN",
         "estado": alta.OK, "capa": alta.VEREDICTO,
         "detalle": "no se detecta nada roto", "nada_que_hacer": True},
    ]
    assert alta._desenlace(chequeos)["clase"] == "viejo"


def test_la_conclusion_sana_declara_nada_que_hacer():
    import api.services.av_agent_alta as alta

    # El paso «⇒ LA CONCLUSIÓN» lleva el flag cuando no hay nada que hacer hoy
    # («sano», y desde §0.cz también «sin_precio»: papel que no operó)…
    assert 'nada_que_hacer=(dx["causa"] in ("sano", "sin_precio"))' \
        in inspect.getsource(alta)
    # …y `_desenlace` lo busca en TODAS las capas, no solo en `prueba`.
    assert 'c for c in chequeos if c.get("nada_que_hacer")' in codigo(alta._desenlace)


def test_todo_diagnostico_que_emite_veredicto_cierra_si_dio_viejo():
    """Los CUATRO puntos que adjuntan `veredicto` llaman al cierre. Si mañana
    nace un quinto y no lo llama, este número lo delata."""
    import api.services.av_agent_alta as alta
    src = inspect.getsource(alta)
    asigna = src.count('out["veredicto"] = _veredicto(')
    cierra = src.count("_cerrar_si_viejo(tk, out)")
    assert asigna == cierra >= 4
