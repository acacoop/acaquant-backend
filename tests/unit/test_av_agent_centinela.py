"""EL CENTINELA: la identidad estable y el ciclo de vida.

Lo que se prueba acá es la frase del user que define el diseño: *«sin pisar lo
que ya reportó y todavía no hice nada»*.
"""
from __future__ import annotations

import inspect

from api.services import av_agent_centinela as c


def test_la_clave_NO_incluye_el_motivo():
    """El motivo lleva números que cambian en cada pasada («hace 12 min» → «hace
    13 min»). Si entrara en la identidad, cada ciclo crearía una fila nueva y
    estaríamos otra vez en el DELETE+INSERT que este módulo vino a eliminar."""
    a = {"tipo": "sin_precio", "ticker": "AO29", "regla": "precio_viejo",
         "motivo": "no se actualiza hace 12 min"}
    b = {**a, "motivo": "no se actualiza hace 13 min"}
    assert c._clave(a) == c._clave(b)


def test_dos_problemas_distintos_del_mismo_bono_son_dos_claves():
    """Un bono puede estar sin precio Y con la moneda mal. Son dos cosas y se
    atienden distinto."""
    base = {"tipo": "sin_precio", "ticker": "AO29"}
    assert c._clave({**base, "regla": "precio_viejo"}) != \
        c._clave({**base, "regla": "sin_punta"})


def test_el_upsert_NO_pisa_abierto_at_ni_visto_at():
    """Son los dos campos que contestan «¿desde cuándo?» y «¿ya lo miré?».
    Refrescarlos en cada ciclo borraría exactamente lo que el user pidió que no
    se pierda."""
    src = inspect.getsource(c.ciclo)
    do_update = src[src.index("DO UPDATE"):src.index("RETURNING")]
    # Se busca la ASIGNACIÓN, no la palabra: el comentario de al lado los nombra
    # justamente para explicar por qué NO se tocan.
    assert "abierto_at =" not in do_update
    assert "visto_at =" not in do_update
    # Lo que SÍ se refresca es el estado de ahora.
    assert "motivo = EXCLUDED.motivo" in do_update and "ultimo_at = now()" in do_update


def test_un_hallazgo_que_vuelve_REABRE_la_misma_fila():
    """Un problema intermitente es UN problema intermitente, no cinco problemas
    distintos — y `veces` es lo que lo delata."""
    src = inspect.getsource(c.ciclo)
    do_update = src[src.index("DO UPDATE"):src.index("RETURNING")]
    assert "resuelto_at = NULL" in do_update
    assert "veces = mercado.av_agent_centinela.veces + 1" in do_update


def test_una_pasada_VACIA_no_resuelve_nada():
    """Si todos los detectores fallan, sus hallazgos faltan por el ERROR y no
    porque se hayan arreglado. Darlos por resueltos sería el peor tipo de
    mentira: silenciosa y optimista."""
    src = inspect.getsource(c.ciclo)
    i_auto = src.index("resuelto_como = 'solo'")
    assert "if hallazgos:" in src[:i_auto], (
        "el auto-resuelto tiene que estar guardado por una pasada no vacía")


def test_el_latido_se_escribe_TAMBIEN_cuando_el_ciclo_falla():
    """Un centinela que solo late cuando todo sale bien se ve idéntico a uno
    muerto — y esa es justo la diferencia que el círculo tiene que mostrar."""
    src = inspect.getsource(c.ciclo)
    assert src.index("_latir(") > src.index("except Exception")


def test_marcar_visto_no_resuelve_ni_esconde():
    """Son dos cosas distintas. Si marcar visto ocultara el hallazgo, nadie lo
    marcaría por miedo a perderlo de vista."""
    src = inspect.getsource(c.marcar_visto)
    assert "visto_at = now()" in src
    assert "resuelto_at" not in src


def test_VIVO_es_una_afirmacion_sobre_ahora():
    """Sale de la EDAD del último latido. Sin esa resta, el círculo quedaría
    verde para siempre después de que el proceso muera."""
    src = inspect.getsource(c.estado)
    assert "edad < cadencia * CICLOS_PERDIDOS" in src


def test_la_tolerancia_sale_del_RITMO_QUE_EL_LATIDO_DECLARO():
    """**El bug del 2026-08-18, con el proceso perfectamente vivo.** El umbral
    era `INTERVALO_RUEDA_S * 3` = 90s fijos, pero fuera de rueda el centinela
    late cada 300s: el círculo salía GRIS a los 110 segundos mientras systemd
    mostraba el daemon `active (running)`.

    El umbral medía un ritmo y el daemon corría a otro. Ahora el latido declara
    su propia cadencia y la tolerancia se deriva de ella — cambiar un intervalo
    no puede volver a desincronizar el semáforo."""
    src = inspect.getsource(c)
    assert "LATIDO_VIVO_S" not in src, "quedó la constante que causaba el bug"
    # La cadencia que se guarda es la MISMA que usa el daemon para dormir.
    assert "proximo = INTERVALO_RUEDA_S if abierto else INTERVALO_CERRADO_S" in src
    assert "cadencia = lat[7] or INTERVALO_RUEDA_S" in inspect.getsource(c.estado)


def test_el_latido_dice_CUANDO_se_apagaria():
    """Que el número esté a la vista es lo que hace que «apagado» se pueda
    verificar en vez de creerse."""
    assert "muere_en_s" in inspect.getsource(c.estado)


def test_el_centinela_no_escribe_en_ninguna_otra_tabla():
    """El user pidió un centinela, no un piloto automático: «que no haga nada de
    solucionar pero que sí me dé las cosas»."""
    src = inspect.getsource(c)
    for tabla in ("mercado.curvas", "portafolio.assets", "mercado.especies"):
        assert f"UPDATE {tabla}" not in src and f"INSERT INTO {tabla}" not in src


def test_lo_nuevo_y_sin_ver_sale_primero():
    """Es lo único de la lista que pide una decisión."""
    assert "(visto_at IS NULL) DESC" in inspect.getsource(c.estado)


# ── La PATA EQUIVOCADA (2026-08-19) ───────────────────────────────────────

_BONO_USD = {"ticker": "MERV - XMEV - AO29 - 24hs", "ticker_corto": "AO29",
             "moneda_eje": "USD", "curva": "soberanos", "valor_nominal": 100}
_SNAP = {"MERV - XMEV - AO29 - 24hs": {"last_price": 139300.0}}
_MEP = 1524.81


def test_si_la_pata_CORRECTA_existe_el_hallazgo_es_ACCIONABLE():
    """**Medido en prod**: de 229 bonos, 3 suscriben una pata distinta de la que
    `mercado.especies` marca como default (AO29, GD46, CO32) — y son justo los
    tres que muestran pesos en una curva en dólares.

    O sea que esto NO era «el bono cotiza así y no hay nada que hacer»: es un
    dato mal cargado, con la pata correcta ya existente y validada."""
    from api.services import av_agent
    h = av_agent.detectar_precio_fuera_de_moneda(
        [_BONO_USD], _SNAP, _MEP, set(),
        {"AO29": "MERV - XMEV - AO29D - 24hs"})
    assert len(h) == 1
    assert h[0]["regla"] == "pata_equivocada"
    assert h[0]["severidad"] == "media"      # no alta: la valuación está bien
    assert h[0]["evidencia"]["sugerido"] == "MERV - XMEV - AO29D - 24hs"


def test_el_hallazgo_AVISA_que_hace_falta_reiniciar_el_motor():
    """El universo del motor se arma al arrancar: cambiar el campo no surte
    efecto hasta el próximo reinicio, y reiniciar en rueda corta el feed de la
    mesa. Una acción que se aplica y no se ve es peor que ninguna."""
    from api.services import av_agent
    h = av_agent.detectar_precio_fuera_de_moneda(
        [_BONO_USD], _SNAP, _MEP, set(),
        {"AO29": "MERV - XMEV - AO29D - 24hs"})
    assert "FUERA DE RUEDA" in h[0]["evidencia"]["ojo"]


def test_sin_pata_default_distinta_sigue_siendo_CONTEXTO_y_no_un_error():
    """Cuando el master ya suscribe la pata que corresponde, que el bono cotice
    en pesos es una característica del instrumento, no un dato mal cargado.
    Llamarlo error sería el falso positivo que enseña a ignorar la lista."""
    from api.services import av_agent
    h = av_agent.detectar_precio_fuera_de_moneda(
        [_BONO_USD], _SNAP, _MEP, set(),
        {"AO29": "MERV - XMEV - AO29 - 24hs"})     # el master YA tiene la default
    assert len(h) == 1 and h[0]["regla"] == "cotiza_en_pesos"
    assert h[0]["severidad"] == "baja"


def test_sin_catalogo_de_especies_no_se_inventa_una_pata():
    """Si la query de especies falla, `defaults` viene vacío y el detector
    vuelve al comportamiento anterior. «No pude mirar» no puede volverse un
    veredicto — tampoco al revés."""
    from api.services import av_agent
    h = av_agent.detectar_precio_fuera_de_moneda([_BONO_USD], _SNAP, _MEP, set(), {})
    assert h[0]["regla"] == "cotiza_en_pesos"
