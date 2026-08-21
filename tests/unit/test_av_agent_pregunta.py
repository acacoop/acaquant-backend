"""¿ACERTÓ QUÉ? — no todo hallazgo es un juicio.

El user, mirando tres filas de `motor_ruidoso` (2026-08-21):

    *«Es inentendible si acertó o no. O sea, ¿acertó QUÉ? Si ni se entiende cuál
     fue el error. Algunos son siempre SÍ claramente, si detecta solo que hay
     algo que está pasando. Pero ¿QUÉ HACEMOS CON ESO?»*

Y el daño va más allá de la confusión:

    JUICIO       el agente DEDUJO una causa y puede errarle → ¿ACERTÓ? es LA
                 pregunta.
    OBSERVACIÓN  el agente COPIÓ un hecho (una línea de ERROR del log, un 500
                 del proveedor). No hay nada que acertar: la respuesta es
                 SIEMPRE que sí.

Esos «siempre sí» llegan a 10/10, la causa se marca `candidata_a_auto` y el
tablero afirma que el agente es infalible en algo donde nunca emitió un juicio.
**La compuerta de la autonomía se abriría con evidencia que no mide nada.**
"""
from __future__ import annotations

from api.services import av_agent


def test_un_ERROR_del_log_no_es_un_juicio():
    """El log lo escribió el motor. Que el patrón esté bien agrupado es un
    detalle de implementación, no una causa que se pueda acertar."""
    assert av_agent.pregunta_de("motor_ruidoso") == av_agent.OBSERVACION


def test_un_diagnostico_de_bono_SI_es_un_juicio():
    for tipo in ("tasa_sospechosa", "precio_moneda", "sin_precio", "falta_en_base"):
        assert av_agent.pregunta_de(tipo) == av_agent.JUICIO, tipo


def test_lo_que_el_agente_DEDUCE_sigue_siendo_juicio_aunque_sea_del_sistema():
    """`tabla_quieta` APRENDE la cadencia y decide que hay atraso — y ya se
    equivocó feo con eso (8 falsos positivos por tablas de evento). Es
    exactamente donde hace falta medirlo."""
    assert av_agent.pregunta_de("tabla_quieta") == av_agent.JUICIO
    assert av_agent.pregunta_de("latencia") == av_agent.JUICIO


def test_ante_la_duda_JUICIO():
    """Pedir un voto de más molesta; dar por observación algo que sí era una
    deducción deja al agente sin medición justo donde puede errarle."""
    assert av_agent.pregunta_de("tipo_que_no_existe") == av_agent.JUICIO
    assert av_agent.pregunta_de("") == av_agent.JUICIO


def test_TODO_tipo_esta_declarado():
    """Igual que `SIN_PUERTA`: un tipo nuevo no puede caer en el default sin que
    alguien lo haya pensado."""
    faltan = set(av_agent.ACCION_POR_TIPO) - set(av_agent.PREGUNTA_POR_TIPO)
    assert not faltan, (
        f"tipos sin declarar en PREGUNTA_POR_TIPO: {faltan}. Caerían en JUICIO "
        f"por default y pedirían «¿acertó?» sin que nadie lo haya decidido.")


def test_no_se_declaran_tipos_FANTASMA():
    sobran = set(av_agent.PREGUNTA_POR_TIPO) - set(av_agent.ACCION_POR_TIPO)
    assert not sobran, f"declarados y no emitidos por nadie: {sobran}"


# ── el voto de una observación NO puede mover la compuerta ───────────────────

def test_una_observacion_vota_como_UTILIDAD_y_no_como_humano():
    """Es el corazón del arreglo: `precision_por_causa` y `resumen` cuentan
    `origen IN ('humano','verificado')`, así que `utilidad` queda afuera de la
    compuerta sin tocar una sola query."""
    import inspect

    from api.routers import ia
    src = inspect.getsource(ia.av_agent_eval)
    assert 'origen="utilidad" if observacion else "humano"' in src
    assert "pregunta_de(body.tipo)" in src


def test_el_ORIGEN_no_viaja_en_el_body():
    """Si el front lo mandara, una pantalla vieja o un `curl` podrían anotar un
    «¿te sirve?» como si fuera un juicio — y eso mueve la autonomía."""
    from api.routers.ia import VotoEval
    assert "origen" not in VotoEval.model_fields
    assert "tipo" in VotoEval.model_fields


def test_la_compuerta_sigue_contando_solo_humano_y_verificado():
    import inspect

    from api.services import av_agent_evals
    for fn in (av_agent_evals.precision_por_causa, av_agent_evals.resumen):
        assert "'humano','verificado'" in inspect.getsource(fn)


def test_un_NO_de_observacion_no_exige_explicacion(monkeypatch):
    """«No me sirve verla» ES la explicación entera. Exigir una nota sobre la
    única respuesta que se puede dar sin investigar es lo que hace que nadie
    conteste."""
    from api.services import av_agent_evals as ev
    monkeypatch.setattr(ev, "_voto_previo", lambda c, x: None)
    monkeypatch.setattr(ev, "invalidate", lambda *a, **k: None)

    class _Cur:
        def execute(self, *a, **k): pass
        def fetchone(self): return (1,)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn:
        def cursor(self): return _Cur()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(ev, "get_pool", lambda: type("P", (), {
        "connection": staticmethod(lambda: _Conn())})())

    r = ev.votar(caso="motor_curvas", dominio="sistema", causa="error_de_motor",
                 acierta=False, origen="utilidad")
    assert r["ok"] is True


def test_un_NO_de_JUICIO_sigue_exigiendo_la_causa_real():
    """De «está mal» no se aprende nada, y eso no cambió."""
    from api.services import av_agent_evals as ev
    r = ev.votar(caso="AL30", dominio="bono", causa="pata_equivocada",
                 acierta=False)
    assert r["ok"] is False and "negativo" in r["error"]


# ── el TEXTO del hallazgo, que era lo primero que el user no entendía ────────

def test_el_titulo_del_log_ya_NO_arranca_con_la_fecha_y_el_logger_repetido():
    """Los primeros 38 caracteres eran andamiaje —fecha, nivel y el nombre del
    logger DUPLICADO por el formateador— y el mensaje real quedaba cortado por
    el «…»."""
    from api.services.logs_sistema import patron
    p = patron("2026-08-21 16:49:03,123 ERROR pg_mirror pg_mirror "
               "market_snapshot: deadlock al escribir 42 filas")
    assert p.startswith("pg_mirror market_snapshot:")
    assert "<fecha>" not in p and "ERROR" not in p
    assert "deadlock" in p


def test_el_nombre_del_modulo_se_conserva_UNA_vez():
    """La primera dice qué módulo habló y sirve; la segunda es ruido."""
    from api.services.logs_sistema import patron
    p = patron("2026-08-21 10:20:11 ERROR core.websocket core.websocket "
               "WS Portfolio: se cayo la conexion")
    assert p.count("core.websocket") == 1


def test_una_linea_sin_prologo_no_se_toca():
    """Preferible un título feo a un título vacío."""
    from api.services.logs_sistema import patron
    assert patron("algo raro sin fecha ni nivel") == "algo raro sin fecha ni nivel"


def test_sacar_el_prologo_NO_cambia_como_se_agrupa():
    """El prólogo es idéntico en todas las líneas de la misma unidad, así que
    quitarlo no puede partir ni fusionar grupos."""
    from api.services.logs_sistema import patron
    a = patron("2026-08-21 10:00:00 ERROR m m falló al escribir 3 filas")
    b = patron("2026-08-21 18:31:02 ERROR m m falló al escribir 900 filas")
    assert a == b


# ── «NUEVO» no se le dice a algo de hace 10 horas ────────────────────────────

def test_recien_dura_dos_horas():
    from api.services import av_agent_centinela as c
    assert c.RECIEN_S == 2 * 60 * 60


def test_el_backend_es_el_que_decide_si_algo_es_RECIEN():
    """El navegador no puede mirar el reloj mientras dibuja, y el criterio tiene
    que ser uno solo."""
    import inspect

    from api.services import av_agent_centinela as c
    assert '"recien"' in inspect.getsource(c.estado)
