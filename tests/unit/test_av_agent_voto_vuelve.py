"""«YA LE MARQUÉ MIL VECES QUE SÍ SIRVE Y NO HACE NADA» (§0.bn).

El user, sobre `job:controles_datos`. Tenía razón dos veces, y por dos bugs
distintos que se veían como uno solo.

    ya_votados()   →  WHERE origen = 'humano'
    la observación →  se guarda con origen = 'utilidad'

El voto SE GUARDABA. La pantalla **no podía verlo nunca**. Cero errores, cero
logs: los botones volvían intactos en cada recarga, para siempre.

La causa de fondo es una sola y ya tiene nombre en este repo: **un filtro
sirviendo a dos preguntas**. `utilidad` se separó de `humano` para que las
observaciones no inflen la compuerta de autonomía —y eso está bien—, pero
«¿esto cuenta para dar autonomía?» y «¿ya me contestaste?» no son la misma
pregunta y quedaron compartiendo un `WHERE`.
"""
from __future__ import annotations

from ._fuente import codigo


def test_la_pantalla_se_acuerda_de_los_dos_tipos_de_voto():
    from api.services import av_agent_evals as ev
    assert set(ev.VOTOS_DE_PERSONA) == {"humano", "utilidad"}
    assert "origen = ANY(%s)" in codigo(ev.ya_votados)
    assert "origen = 'humano'" not in codigo(ev.ya_votados)


def test_la_COMPUERTA_sigue_contando_solo_lo_humano():
    """El arreglo NO puede aflojar el gate: una observación siempre se contesta
    que sí, y esos «siempre sí» llegarían a 10/10 marcando la causa como lista
    para automatizar con evidencia que no mide nada."""
    from api.services import av_agent_evals as ev
    src = codigo(ev.resumen) if hasattr(ev, "resumen") else ""
    assert "utilidad" not in src, (
        "la compuerta empezó a contar los votos de utilidad")


def test_el_dedup_mira_la_MISMA_clase_de_voto_que_se_esta_emitiendo():
    """Mismo bug, un nivel más abajo: `_voto_previo` tenía el origen clavado en
    `'humano'`, así que para una observación el previo NUNCA aparecía y cada
    «✔ sirve» escribía una fila nueva. La dedup existía y no dedupeaba nada."""
    from api.services import av_agent_evals as ev
    assert "origen = %s" in codigo(ev._voto_previo)
    assert "_voto_previo(caso, causa, origen)" in codigo(ev.votar)


def test_ES_RUIDO_deja_de_ser_un_boton_que_no_hace_nada():
    """Estos votos se escribían y **no los leía nadie** — medido: cero
    consultas en todo el repo. Un botón que guarda una opinión y deja la fila
    donde estaba es peor que no tenerlo, porque parece que hizo algo."""
    from api.services import av_agent_evals as ev
    from api.services.av_agent_vista import vista
    src = codigo(vista)
    assert "es_ruido()" in src
    assert '"es_ruido": n_ruido' in src
    # Se lee del ÚLTIMO voto → «cambiar» en la pantalla lo devuelve.
    assert "DISTINCT ON (caso, causa)" in codigo(ev.es_ruido)


def test_lo_escondido_se_CUENTA():
    """Un filtro que oculta sin decir cuánto oculta es lo mismo que truncar en
    silencio — y esconder un problema real para siempre es el riesgo entero de
    esta función."""
    from api.services.av_agent_vista import vista
    assert "n_ruido = sum(" in codigo(vista)


def test_el_ruido_se_MARCA_y_no_se_saca_del_payload():
    """Si la fila no viaja, no hay forma de recuperarla desde la app. Esconder
    sin poder volver atrás es cómo se consigue que nadie marque nada."""
    from api.services.av_agent_vista import vista
    src = codigo(vista)
    assert 'h["es_ruido"] = True' in src
    assert "hallazgos = [h for h in hallazgos if not h" not in src
