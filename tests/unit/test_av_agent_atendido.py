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
    """Si el override apunta a un modo que ninguna acción implementa, el front
    dibuja un botón que no existe del otro lado — el mismo modo de falla que
    esto vino a arreglar, con otro disfraz."""
    from api.services.av_agent_hacer import ACCIONES
    modos = {a.id.split(".")[-1] for a in ACCIONES.values()}
    assert "apuntar_pata" in modos
    assert set(av_agent.ACCION_POR_REGLA.values()) == {"apuntar"}


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
    from unittest.mock import patch

    from api.services import av_agent_vista as v
    with patch.object(v, "_hallazgos_ultima_corrida", lambda: (hallazgos, "hoy")), \
         patch.object(v, "_aplicados_ok", lambda: set(aplicados)), \
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


def test_VOTAR_NO_marca_como_hecho_lo_que_tiene_boton():
    """⚠️ **La confusión que habría sido peor que el problema.** El voto juzga al
    AGENTE; el arreglo cambia el dato. Si el voto marcara la fila como hecha, los
    17 BOPREALes desaparecían de la vista **estando rotos**."""
    d = _vista([_h("BPOA7", "precio_moneda", "pata_equivocada", "apuntar")],
               votados={("BPOA7", "pata_equivocada"): True})
    h = d["hallazgos"][0]
    assert h["ya_votado"] is True
    assert h["atendido"] == "", "votar no arregla nada"
    assert d["atendidos"] == 0


def test_APLICAR_si_lo_marca():
    d = _vista([_h("BPOA7", "precio_moneda", "pata_equivocada", "apuntar")],
               aplicados={"BPOA7"})
    assert d["hallazgos"][0]["atendido"] == "aplicado"
    assert d["atendidos"] == 1


def test_SIN_BOTON_el_voto_ALCANZA():
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


def test_si_no_se_puede_leer_lo_aplicado_TODO_queda_pendiente(monkeypatch):
    """Ante la duda se muestra de más: una fila de sobra molesta, una fila
    escondida que estaba rota no se ve nunca."""
    from api.services import av_agent_vista as v
    monkeypatch.setattr(v, "get_pool",
                        lambda: (_ for _ in ()).throw(RuntimeError("sin base")))
    assert v._aplicados_ok() == set()
