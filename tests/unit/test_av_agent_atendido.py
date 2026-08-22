"""EL BOTÓN QUE NO ARREGLABA NADA, Y LA LISTA QUE NO SE PODÍA TRABAJAR.

Dos pedidos del user (2026-08-21), mirando los BOPREALes en **17/17 votos**:

    *«¡otra vez lo mismo, ya lo completé 40 veces y sigue apareciendo!»*
    *«que ENCONTRÓ muestre por defecto lo que NO hice; que estos queden en
    ENCONTRÓ pero marcados como ya hechos»*

Y la causa del primero no era el detector: **era el botón**. `precio_moneda`
agrupa dos problemas que se arreglan distinto, la acción salía del TIPO, y los
dos mostraban «BUSCAR LA PATA USD» — que para `pata_equivocada` pide una pata
que ya cotizaba y deja el master apuntando a la de pesos.

> Un botón que no arregla el problema de esa fila es peor que no tenerlo:
> promete, no cumple, y no da un solo error.
"""
from __future__ import annotations

from api.services import av_agent

# ── la REGLA gana sobre el TIPO ──────────────────────────────────────────────

def test_pata_equivocada_ya_NO_abre_el_boton_de_buscar():
    """El que volvía 17 veces. Buscar la pata no toca `mercado.curvas`."""
    assert av_agent.accion_de("precio_moneda", "pata_equivocada") == "apuntar"


def test_cotiza_en_pesos_SIGUE_yendo_a_buscar_la_pata():
    """Es el otro problema del mismo tipo: ahí la pata NO existe todavía, así
    que buscarla es exactamente el arreglo."""
    assert av_agent.accion_de("precio_moneda", "cotiza_en_pesos") == "pata"


def test_sin_regla_manda_el_tipo():
    assert av_agent.accion_de("precio_moneda", "") == "pata"
    assert av_agent.accion_de("falta_en_base", "loquesea") == "alta"


def test_un_tipo_sin_accion_sigue_sin_accion():
    assert av_agent.accion_de("dato_partido", "") is None


def test_toda_regla_con_accion_PROPIA_tiene_su_accion_registrada():
    """Si el override apunta a un modo que NADIE implementa, el front dibuja un
    botón que no existe del otro lado — el mismo modo de falla que esto vino a
    arreglar, con otro disfraz.

    Un modo vale de DOS maneras, y las dos se verifican DERIVANDO en vez de
    listar: o lo implementa una acción que escribe (`av_agent_hacer.ACCIONES`),
    o lo implementa una cadena de SOLO LECTURA (`api/services/av_agent_<modo>.py`
    con su `diagnosticar()`). Enumerar los modos válidos a mano haría que el
    tercero nazca sin cobertura, que es justo lo que este test evita.
    """
    import importlib

    from api.services.av_agent_hacer import ACCIONES
    modos = {a.id.split(".")[-1] for a in ACCIONES.values()}
    assert "apuntar_pata" in modos

    for regla, modo in av_agent.ACCION_POR_REGLA.items():
        if any(m.startswith(modo) for m in modos):
            continue                      # lo implementa una acción que escribe
        try:
            mod = importlib.import_module(f"api.services.av_agent_{modo}")
        except ImportError:                                  # pragma: no cover
            raise AssertionError(
                f"«{regla}» apunta al modo «{modo}» y no existe ni una acción "
                f"ni api/services/av_agent_{modo}.py — el botón no lleva a "
                f"ningún lado") from None
        assert callable(getattr(mod, "diagnosticar", None)), (
            f"av_agent_{modo}.py existe pero no expone `diagnosticar()`")


# ── una acción, un sujeto: la puerta de la FILA ──────────────────────────────

def test_uno_no_existe_la_accion():
    from api.services import av_agent_hacer as h
    r = h.uno("no.existe", "BPOA7")
    assert r["ok"] is False and "no existe" in r["error"]


def test_uno_sin_sujeto_no_hace_nada():
    from api.services import av_agent_hacer as h
    assert h.uno("mercado.apuntar_pata", "  ")["ok"] is False


def test_uno_SIN_aplicar_no_escribe(monkeypatch):
    """El paso SIMULAR de la fila: aprobar a ciegas no es aprobar."""
    from api.services import av_agent_hacer as h
    monkeypatch.setattr(h, "_casos_frescos", lambda c: ([{
        "key": "BPOA7", "ticker": "BPOA7", "curva": "bopreales",
        "simbolo": "MERV - XMEV - BPOA7 - 24hs",
        "sugerido": "MERV - XMEV - BPA7D - 24hs"}], ""))
    monkeypatch.setattr(h, "_guardar",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("escribió")))
    r = h.uno("mercado.apuntar_pata", "BPOA7")
    assert r["ok"] is True and r["simulado"] is True
    assert r["propuesta"]["propuesto"] == "MERV - XMEV - BPA7D - 24hs"
    assert r["propuesta"]["antes"] == "MERV - XMEV - BPOA7 - 24hs"


def test_uno_YA_NO_ESTA_no_es_un_error(monkeypatch):
    """Entre la corrida y el click el problema pudo resolverse solo. Decirlo
    como error haría creer que el botón falló."""
    from api.services import av_agent_hacer as h
    monkeypatch.setattr(h, "_casos_frescos", lambda c: ([], ""))
    r = h.uno("mercado.apuntar_pata", "BPOA7")
    assert r["ok"] is True and r["ya_no_esta"] is True and r["aplicadas"] == 0


def test_uno_solo_toca_EL_sujeto_pedido(monkeypatch):
    """Sobre 17 BOPREALes rotos, apretar el botón de uno no puede reescribir el
    símbolo de los otros 16."""
    from api.services import av_agent_hacer as h
    casos = [{"key": t, "ticker": t, "curva": "bopreales",
              "simbolo": f"MERV - XMEV - {t} - 24hs",
              "sugerido": f"MERV - XMEV - {t}D - 24hs"}
             for t in ("BPOA7", "BPOA8", "BPOB7")]
    monkeypatch.setattr(h, "_casos_frescos", lambda c: (casos, ""))
    r = h.uno("mercado.apuntar_pata", "BPOA8")
    assert r["propuesta"]["sujeto"] == "BPOA8"


def test_uno_no_gasta_un_token(monkeypatch):
    """Una fila no espera a que piense un modelo, y menos para un valor que la
    regla ya sabe."""
    import inspect

    from api.services import av_agent_hacer as h
    assert "_solo_regla" in inspect.getsource(h.uno)


def test_uno_aplica_por_la_MISMA_puerta_que_la_tab():
    """No es un segundo camino a la escritura: si lo fuera, el libro y la
    verificación funcionarían distinto según por dónde entraste."""
    import inspect

    from api.services import av_agent_hacer as h
    src = inspect.getsource(h.uno)
    assert "aplicar([" in src and "_casos_frescos" in src


# ── lo ATENDIDO se marca, no se borra ────────────────────────────────────────

def _vista(hallazgos, *, votados=None, aplicados=frozenset()):
    """⚠️ **El estado ya NO se consulta aparte.** Antes había un
    `_aplicados_ok()` que leía `av_agent_propuestas` — una de las cinco
    derivaciones ad-hoc que se contradecían (§0.bc). Ahora viene con el
    hallazgo, en el mismo JOIN, así que el harness lo inyecta ahí."""
    from unittest.mock import patch

    from api.services import av_agent_vista as v
    from core import ciclo
    for h in hallazgos:
        if h["ticker"] in aplicados:
            h["estado_item"] = ciclo.EN_CURSO
    with patch.object(v, "_hallazgos_ultima_corrida", lambda: (hallazgos, "hoy")), \
         patch.object(v.av_agent_evals, "ya_votados", lambda: dict(votados or {})), \
         patch.object(v, "avisos", lambda: []), \
         patch.object(v, "mensajes", lambda: []), \
         patch.object(v, "_decididas", lambda: []), \
         patch.object(v, "_ignorados", lambda: []), \
         patch("api.services.av_agent_acciones.listar", lambda: []), \
         patch("api.services.av_agent_preguntas.abiertas", lambda: []), \
         patch("api.services.av_agent_preguntas.pendientes_de_aplicar", lambda: []):
        return v.vista()


def _h(ticker, tipo, regla, accion):
    return {"ticker": ticker, "tipo": tipo, "regla": regla, "accion": accion,
            "motivo": "", "de_quien": "nuestro"}


def test_VOTAR_con_arreglo_pendiente_NO_saca_la_fila():
    """⚠️⚠️ **VOTAR NO ES ARREGLAR — esta regla ya estuvo dos veces al revés.**

    Primera versión: el voto no contaba como atendido (con botón), y 17
    BOPREALes votados taparon la lista → §0.bq la dio vuelta («si votó, lo
    hizo»). Segunda versión: con los botones ARREGLAR ya cableados, votar SÍ
    sacaba la fila — y el user (2026-08-22): *«toqué que SÍ y desapareció, NO
    ME DEJÓ ARREGLARLO. Tienen que ser dos checks distintos: el acertó
    persiste, pero la fila tiene que seguir hasta que se arregle»*.

    La síntesis que queda: son DOS checks. El voto persiste y no se
    re-pregunta (`ya_votado`); la fila sale de la lista recién cuando el
    arreglo se APLICA o el detector confirma. El contexto que decide es si la
    fila TIENE una acción pendiente.
    """
    d = _vista([_h("NDT25", "tasa_sospechosa", "sin_tea_con_precio", "arreglo")],
               votados={("NDT25", "sin_tea_con_precio"): True})
    h = d["hallazgos"][0]
    assert h["ya_votado"] is True, "el voto PERSISTE: no se vuelve a preguntar"
    assert h["atendido"] == "", "con arreglo pendiente, votar NO la saca"
    assert d["atendidos"] == 0


def test_APLICAR_es_lo_que_la_saca_aunque_ya_este_votada():
    """El segundo check: la fila votada sale recién cuando el arreglo se
    aplica — APLICADO es el estado que manda."""
    aplicado = _vista([_h("BPOA7", "precio_moneda", "pata_equivocada", "apuntar")],
                      votados={("BPOA7", "pata_equivocada"): True},
                      aplicados={"BPOA7"})
    assert aplicado["hallazgos"][0]["atendido"] == "aplicado"


def test_APLICAR_si_lo_marca():
    d = _vista([_h("BPOA7", "precio_moneda", "pata_equivocada", "apuntar")],
               aplicados={"BPOA7"})
    assert d["hallazgos"][0]["atendido"] == "aplicado"
    assert d["atendidos"] == 1


def test_SIN_BOTON_el_voto_TAMBIEN_ALCANZA():
    """Si no hay nada que apretar, votar es lo único que se puede hacer con esa
    fila — dejarla arriba después es pedirle al user que la mire de nuevo para
    nada."""
    d = _vista([_h("XX", "dato_partido", "preferencia", None)],
               votados={("XX", "preferencia"): True})
    assert d["hallazgos"][0]["atendido"] == "votado"


def test_sin_boton_y_SIN_votar_sigue_pendiente():
    d = _vista([_h("XX", "dato_partido", "preferencia", None)])
    assert d["hallazgos"][0]["atendido"] == ""


def test_lo_atendido_SIGUE_EN_LA_LISTA():
    """«Que queden en ENCONTRÓ pero marcados como ya hechos» — marcarlo es una
    cosa, borrarlo es otra."""
    d = _vista([_h("BPOA7", "precio_moneda", "pata_equivocada", "apuntar")],
               aplicados={"BPOA7"})
    assert len(d["hallazgos"]) == 1


def test_sin_estado_el_hallazgo_queda_PENDIENTE():
    """Ante la duda se muestra de más: una fila de sobra molesta, una fila
    escondida que estaba rota no se ve nunca. Si el JOIN no encontró item
    (todavía no se espejó, o la base no contestó), `estado_item` viene `None` y
    el hallazgo sigue siendo trabajo."""
    d = _vista([_h("BPOA7", "precio_moneda", "pata_equivocada", "apuntar")])
    assert d["hallazgos"][0]["atendido"] == ""


def test_el_estado_YA_NO_se_consulta_aparte():
    """La derivación se fue: una query menos y una fuente de verdad menos."""
    from api.services import av_agent_vista as v

    from ._fuente import codigo
    assert not hasattr(v, "_aplicados_ok")
    # `codigo()` saca comentarios y docstring: los comentarios NOMBRAN la tabla
    # justo para decir que ya no se consulta, y sin eso el test se caza solo.
    assert "av_agent_propuestas" not in codigo(v.vista)
    assert "av_agent_propuestas" not in codigo(v._hallazgos_ultima_corrida)


def test_RESUELTO_tambien_cuenta_como_atendido():
    """El detector volvió a mirar y ya no está: no hay nada más que hacer."""
    from core import ciclo
    h = _h("BPOA7", "precio_moneda", "pata_equivocada", "apuntar")
    h["estado_item"] = ciclo.RESUELTO
    assert _vista([h])["hallazgos"][0]["atendido"] == "aplicado"


def test_el_masivo_conoce_TODAS_las_puertas_que_accion_de_devuelve():
    """⚠️⚠️ **LA CUARTA COPIA DE LA TABLA DE RUTEO** (§0.cb).

    `av_agent_masivo` tenía su propio `if/elif` con CUATRO modos mientras
    `accion_de()` ya devolvía OCHO. Los otros cuatro caían al `else` y el
    informe los declaraba «SIN PUERTA — el agente los ve y todavía no sabe
    tocarlos». Medido en el masivo #14: **13 de 26 «sin puerta» tenían acción
    desde hacía días** (11 `pata_equivocada` + 2 `sin_espejo_en_assets`).

    O sea: el agente decía que no sabía hacer algo que sabía hacer, y el
    informe pedía construir lo que ya estaba construido. No falla nada — es la
    firma de siempre.
    """
    from api.services.av_agent_masivo import PUERTAS
    modos = set(av_agent.ACCION_POR_TIPO.values()) | set(av_agent.ACCION_POR_REGLA.values())
    modos.discard(None)
    faltan = modos - set(PUERTAS)
    assert not faltan, (
        f"modos que el hallazgo puede pedir y el masivo no sabe abrir: {faltan} "
        f"— van a salir como «sin puerta» siendo mentira")


def test_ninguna_puerta_del_masivo_escribe():
    """El masivo diagnostica 92 bonos de una: si una puerta aplicara, una
    corrida de rutina serían 92 escrituras que nadie aprobó."""
    import inspect

    from api.services.av_agent_masivo import PUERTAS
    for modo, fn in PUERTAS.items():
        src = inspect.getsource(fn)
        assert "aplicar_ya=True" not in src, f"«{modo}» aplicaría"
        assert "aplicar=True" not in src, f"«{modo}» aplicaría"
        # `pedir()` siembra la especie y suscribe — es escritura.
        assert ".pedir(" not in src, f"«{modo}» llama a pedir(), que escribe"
