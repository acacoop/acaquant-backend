"""Tests de api/services/asistente_tools.py — tools read-only del asistente.

SQL mockeado (no tocan la DB). Lo que congelan:
- token-in: la ficha se resuelve a id real SOLO dentro del perímetro.
- token-out: el resultado que vuelve al LLM jamás contiene el id ni el
  nombre real de la cuenta — solo la ficha.
- degradación: cuenta irresoluble / sin datos / tool que explota → mensaje
  claro, nunca excepción ni datos filtrados.
"""
from __future__ import annotations

import pytest

from api.services import asistente_tools as at
from core import pii_gateway as pg

CATALOGO_FAKE = {
    "ids": {"805"},
    "nombres": {"juan perez": "805"},
    "tokens": {"perez": "805"},
    "documentos": set(),
    "operadores": {"martin operetti": "Martin Operetti"},
    "operadores_tokens": {"operetti": "Martin Operetti"},
}


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setattr(pg, "_catalogo", lambda: CATALOGO_FAKE)
    monkeypatch.setattr(at, "_fecha_snapshot", lambda: "2026-07-21")
    monkeypatch.setattr(at, "_aum_totales", lambda fecha: (1_500_000_000.0, 42))
    monkeypatch.setattr(at, "_aum_por_segmento",
                        lambda fecha, top=5: [("PRODUCTORES", 900_000_000.0),
                                              ("SIN SEGMENTO", 600_000_000.0)])
    monkeypatch.setattr(at, "_aum_cuenta", lambda fecha, idc: 12_000_000.0)
    monkeypatch.setattr(at, "_pnl_cuenta", lambda idc: {
        "totales": {"pnl_no_realizado": 500_000.0, "pnl_pasivo": 100_000.0,
                    "pnl_realizado_dia": 0.0, "pnl_total": 600_000.0},
        "computed_at": "2026-07-21 11:00:00+00",
    })


def _mapping_con_cliente():
    _limpio, mapping = pg.tokenize("cómo viene Juan Perez")
    return mapping


def test_resumen_mesa_trae_agregados_sin_nombres():
    r = at.resumen_mesa()
    assert "1.50 mil millones" in r and "42" in r
    assert "PRODUCTORES" in r and "60.0%" in r
    assert "805" not in r and "perez" not in r.lower()


def test_resumen_mesa_sin_snapshot(monkeypatch):
    monkeypatch.setattr(at, "_fecha_snapshot", lambda: None)
    assert "sin datos" in at.resumen_mesa()


def test_rendimiento_cuenta_resuelve_ficha_en_perimetro(monkeypatch):
    mapping = _mapping_con_cliente()
    ficha = next(f for f in mapping["fichas"] if f.startswith("CLIENTE_"))
    resuelto = {}
    original = at._aum_cuenta

    def espia(fecha, idc):
        resuelto["id"] = idc
        return original(fecha, idc)

    monkeypatch.setattr(at, "_aum_cuenta", espia)
    r = at.rendimiento_cuenta(ficha, mapping=mapping)
    assert resuelto["id"] == "805"          # la resolución pasó por el perímetro
    assert ficha in r                        # la respuesta habla en fichas
    assert "805" not in r and "Perez" not in r  # el id/nombre JAMÁS vuelve al LLM
    assert "12.0 millones" in r and "0.60 millones" not in r  # pnl_total formateado aparte


def test_rendimiento_cuenta_ficha_irresoluble():
    r = at.rendimiento_cuenta("CLIENTE_99", mapping={"fichas": {}})
    assert "no pude identificar" in r


def test_ejecutar_dispatcher_y_token_out():
    mapping = _mapping_con_cliente()
    ficha = next(f for f in mapping["fichas"] if f.startswith("CLIENTE_"))
    r = at.ejecutar("rendimiento_cuenta", {"ficha_cuenta": ficha}, mapping=mapping)
    assert ficha in r and "805" not in r and "Perez" not in r


# ── Tanda 0 de la auditoría: el dispatcher sabe QUIÉN pregunta ─────────────

def test_ejecutar_pasa_el_usuario_a_las_tools():
    """Sin el usuario adentro de la tool, un permiso POR USUARIO (Control
    Comercial, que NO mira la matriz de roles) no se puede chequear y el chat
    se vuelve una puerta trasera. Hallazgo de la auditoría 2026-07-21."""
    import inspect
    sig = inspect.signature(at.ejecutar)
    assert "usuario" in sig.parameters


def test_control_comercial_es_fail_closed(monkeypatch):
    assert at.puede_control_comercial(None) is False       # sin usuario, no
    monkeypatch.setattr("core.roles.user_has_control_comercial",
                        lambda e: (_ for _ in ()).throw(RuntimeError("db")))
    assert at.puede_control_comercial("x@y.com") is False  # ante error, no
    monkeypatch.setattr("core.roles.user_has_control_comercial", lambda e: True)
    assert at.puede_control_comercial("x@y.com") is True


def test_asistente_le_pasa_el_email_al_dispatcher(monkeypatch):
    """El circuito completo: responder() → completar_con_tools → ejecutar."""
    from api.services import asistente as asx
    from core import ai, llm
    from core import pii_gateway as pg

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(pg, "_catalogo", lambda: {
        "ids": set(), "nombres": {}, "tokens": {}, "documentos": set(),
        "operadores": {}, "operadores_tokens": {}})
    monkeypatch.setattr(pg, "cargar_mapping", lambda c, e: pg._mapping_nuevo())
    monkeypatch.setattr(pg, "guardar_mapping", lambda c, e, m: None)
    monkeypatch.setattr(asx, "_cargar_historial", lambda c: [])
    monkeypatch.setattr(asx, "_persistir", lambda c, e, t: None)
    monkeypatch.setattr(ai, "motivo_presupuesto", lambda u: None)
    monkeypatch.setattr(ai, "_trazar", lambda *a, **kw: 1)
    visto = {}
    monkeypatch.setattr(at, "ejecutar",
                        lambda n, a, **kw: visto.update(kw) or "ok")

    def chat_fake(mensajes, **kw):
        return llm.RespuestaLLM(ok=True, texto="listo", mensaje={"content": "x"})

    monkeypatch.setattr(llm, "chat", chat_fake)
    # forzamos una llamada a tool para ver qué recibe el dispatcher
    monkeypatch.setattr(ai, "completar_con_tools",
                        lambda tarea, **kw: (kw["ejecutar"]("resumen_mesa", {}), 1, ""))
    asx.responder(mensaje="hola", email="jefe@acavalores.com.ar")
    assert visto["usuario"] == "jefe@acavalores.com.ar"


def test_ejecutar_tool_desconocida():
    assert "desconocida" in at.ejecutar("drop_tables", {}, mapping={"fichas": {}})


# ── quien_es: cliente vs operador NO se adivina (caso real 2026-07-21) ──────

def _mock_personas(monkeypatch, cuentas, operadores):
    import api.services.copiloto.navegacion as nv
    monkeypatch.setattr(nv, "_cuentas", lambda: cuentas)
    monkeypatch.setattr(nv, "_operadores", lambda: operadores)


def test_quien_es_distingue_cliente_de_operador(monkeypatch):
    _mock_personas(monkeypatch, [("805", "MOLLO, NICOLAS EZEQUIEL")],
                   [("jc@aca.com", "Javier Curzel")])
    mapping = {"fichas": {"CLIENTE_1": "nicolas mollo", "CLIENTE_2": "javier curzel"}}
    r1 = at.ejecutar("quien_es", {"ficha": "CLIENTE_1"}, mapping=mapping)
    assert "CUENTA de cliente" in r1 and "MOLLO" not in r1   # habla en fichas
    r2 = at.ejecutar("quien_es", {"ficha": "CLIENTE_2"}, mapping=mapping)
    assert "OPERADOR" in r2 and "Curzel" not in r2


def test_quien_es_ambiguo_manda_a_preguntar(monkeypatch):
    _mock_personas(monkeypatch, [("805", "MOLLO, NICOLAS EZEQUIEL")],
                   [("mm@aca.com", "MOLLO, NICOLAS EZEQUIEL")])
    r = at.ejecutar("quien_es", {"ficha": "CLIENTE_1"},
                    mapping={"fichas": {"CLIENTE_1": "mollo"}})
    assert "AMBIGUO" in r and "preguntale al usuario" in r.lower()


def test_quien_es_desconocido(monkeypatch):
    _mock_personas(monkeypatch, [], [])
    r = at.ejecutar("quien_es", {"ficha": "CLIENTE_9"},
                    mapping={"fichas": {"CLIENTE_9": "nadie"}})
    assert "no encontré" in r


def test_ejecutar_tool_que_explota_no_filtra(monkeypatch):
    def _boom():
        raise RuntimeError("secreto interno: cuenta 805 de Juan Perez")
    monkeypatch.setattr(at, "resumen_mesa", _boom)
    r = at.ejecutar("resumen_mesa", {}, mapping={"fichas": {}})
    assert "805" not in r and "Perez" not in r
    assert "falló" in r


def test_token_out_tacha_identidad_que_colara(monkeypatch):
    """Si una tool devolviera un nombre real por accidente, la aduana lo
    tacha ANTES de volver al LLM (cinturón y tirantes)."""
    monkeypatch.setattr(at, "resumen_mesa", lambda: "el mayor tenedor es Juan Perez")
    r = at.ejecutar("resumen_mesa", {}, mapping=pg._mapping_nuevo())
    assert "Perez" not in r and "CLIENTE_" in r


def test_schemas_declarados():
    nombres = {t["function"]["name"] for t in at.TOOLS}
    assert nombres == {"resumen_mesa", "rendimiento_cuenta", "quien_es",
                       "aum_historico", "volumen_operado", "aranceles_consolidado"}
    rc = next(t for t in at.TOOLS if t["function"]["name"] == "rendimiento_cuenta")
    assert "ficha_cuenta" in rc["function"]["parameters"]["properties"]
    vo = next(t for t in at.TOOLS if t["function"]["name"] == "volumen_operado")
    assert vo["function"]["parameters"]["required"] == ["desde", "hasta", "por"]
    # la cartera del título es dimensión desde 2026-07-21
    assert "cartera" in vo["function"]["parameters"]["properties"]["por"]["enum"]


# ── consolidados (volumen / aranceles por dimensión) ─────────────────────────

def _mock_consolidado(monkeypatch, esperado: dict):
    capturado = {}

    def fake(**kw):
        capturado.update(kw)
        return esperado

    import api.services.operaciones_sql as ops
    monkeypatch.setattr(ops, "ops_consolidado", fake)
    return capturado


def test_volumen_operado_formatea_y_pasa_params(monkeypatch):
    capturado = _mock_consolidado(monkeypatch, {
        "metrica": "bruto", "por": "mercado", "desde": "2026-01-01",
        "hasta": "2026-06-30", "moneda": "ARS",
        "filas": [{"clave": "BYMA", "valor": 900_000_000.0, "n": 1200},
                  {"clave": "MAV", "valor": 100_000_000.0, "n": 300}],
        "total": 1_000_000_000.0,
    })
    r = at.ejecutar("volumen_operado",
                    {"desde": "2026-01-01", "hasta": "2026-06-30", "por": "mercado",
                     "excluir_segmento": "AGRO"}, mapping={"fichas": {}})
    assert capturado["metrica"] == "bruto"
    assert capturado["excluir_segmento"] == "AGRO"
    assert "BYMA" in r and "90.0%" in r and "1200 boletos" in r
    assert "TOTAL" in r


def test_aranceles_consolidado_usa_metrica_arancel(monkeypatch):
    capturado = _mock_consolidado(monkeypatch, {
        "metrica": "arancel", "por": "mercado", "desde": "2026-01-01",
        "hasta": "2026-06-30", "moneda": "ARS",
        "filas": [{"clave": "BYMA", "valor": 5_000_000.0, "n": 800}],
        "total": 5_000_000.0,
    })
    r = at.ejecutar("aranceles_consolidado",
                    {"desde": "2026-01-01", "hasta": "2026-06-30", "por": "mercado"},
                    mapping={"fichas": {}})
    assert capturado["metrica"] == "arancel"
    assert "aranceles por mercado" in r and "5.0 millones" in r


def test_consolidado_dolariza(monkeypatch):
    """Caso real: pidió el cuadro dolarizado y el asistente dijo 'no tengo
    cotización'. SÍ se puede: cada boleto guarda su propio TC."""
    capturado = _mock_consolidado(monkeypatch, {
        "metrica": "arancel", "por": "operacion", "desde": "2025-07-01",
        "hasta": "2026-06-30", "moneda": "USD",
        "filas": [{"clave": "Ventas Futuros A3", "valor": 2_000_000.0, "n": 10}],
        "total": 2_000_000.0,
    })
    r = at.ejecutar("aranceles_consolidado",
                    {"desde": "2025-07-01", "hasta": "2026-06-30",
                     "por": "operacion", "moneda": "USD"}, mapping={"fichas": {}})
    assert capturado["moneda"] == "USD"
    assert "USD" in r and "ARS" not in r          # la unidad acompaña al número
    assert "TC de cada boleto" in r               # y se explica de dónde sale


def test_consolidado_moneda_invalida_cae_a_ars(monkeypatch):
    capturado = _mock_consolidado(monkeypatch, {
        "metrica": "bruto", "por": "mercado", "desde": "2026-01-01",
        "hasta": "2026-01-31", "moneda": "ARS", "filas": [], "total": 0.0})
    at.ejecutar("volumen_operado", {"desde": "2026-01-01", "hasta": "2026-01-31",
                                    "por": "mercado", "moneda": "EUR"},
                mapping={"fichas": {}})
    assert capturado["moneda"] == "ARS"


# ── AuM histórico (la tool que faltaba: promedio/mediana de un período) ─────

def test_aum_historico_calcula_estadisticas(monkeypatch):
    serie = [("2026-06-01", 100.0), ("2026-06-02", 300.0), ("2026-06-03", 200.0)]
    capturado = {}

    def fake(desde, hasta, cartera, id_cuenta):
        capturado.update(desde=desde, hasta=hasta, cartera=cartera, id_cuenta=id_cuenta)
        return serie

    monkeypatch.setattr(at, "_aum_serie_diaria", fake)
    r = at.ejecutar("aum_historico",
                    {"desde": "2026-06-01", "hasta": "2026-06-30", "cartera": "FCI"},
                    mapping={"fichas": {}})
    assert capturado["cartera"] == "FCI" and capturado["id_cuenta"] is None
    assert "promedio: 200 ARS" in r        # (100+300+200)/3
    assert "mediana:  200 ARS" in r
    assert "máximo:   300 ARS (2026-06-02)" in r
    assert "3 días con snapshot" in r


def test_aum_historico_sin_datos_lo_dice(monkeypatch):
    monkeypatch.setattr(at, "_aum_serie_diaria", lambda *a: [])
    r = at.ejecutar("aum_historico", {"desde": "2019-01-01", "hasta": "2019-01-31"},
                    mapping={"fichas": {}})
    assert "no hay snapshots" in r


def test_aum_historico_sin_periodo():
    r = at.ejecutar("aum_historico", {"cartera": "FCI"}, mapping={"fichas": {}})
    assert "necesito el período" in r


def test_consolidado_dimension_invalida_es_jaula():
    from api.services.operaciones_sql import ops_consolidado
    r = ops_consolidado(metrica="bruto", desde="2026-01-01", hasta="2026-06-30",
                        por="; DROP TABLE operaciones")
    assert "invalida" in r["error"]


def test_consolidado_por_operador_ficha_los_nombres(monkeypatch):
    """Decisión b (2026-07-21): los EMPLEADOS tampoco salen — la dimensión
    operador vuelve al LLM con fichas OPERADOR_n, jamás nombres."""
    _mock_consolidado(monkeypatch, {
        "metrica": "arancel", "por": "operador", "desde": "2026-01-01",
        "hasta": "2026-06-30", "moneda": "ARS",
        "filas": [{"clave": "Martin Operetti", "valor": 3_000_000.0, "n": 40},
                  {"clave": "(sin operador)", "valor": 500_000.0, "n": 9}],
        "total": 3_500_000.0,
    })
    mapping = pg._mapping_nuevo()
    r = at.ejecutar("aranceles_consolidado",
                    {"desde": "2026-01-01", "hasta": "2026-06-30", "por": "operador"},
                    mapping=mapping)
    assert "Operetti" not in r and "OPERADOR_1" in r
    assert "(sin operador)" in r          # la huérfana no es una identidad
    assert mapping["fichas"]["OPERADOR_1"] == "Martin Operetti"  # detokeniza al user


def test_consolidado_filtra_por_cuenta_de_cliente(monkeypatch):
    """'¿cuánto operó tal cliente?' — la pregunta que NO tenía herramienta y
    terminaba respondida con el patrimonio (caso real 2026-07-21)."""
    import api.services.copiloto.navegacion as nv
    monkeypatch.setattr(nv, "_cuentas", lambda: [("805", "CURZEL, JAVIER")])
    monkeypatch.setattr(nv, "_operadores", lambda: [])
    capturado = _mock_consolidado(monkeypatch, {
        "metrica": "bruto", "por": "operacion", "desde": "2026-06-01",
        "hasta": "2026-06-30", "moneda": "ARS",
        "filas": [{"clave": "Compras", "valor": 5_000_000.0, "n": 12}],
        "total": 5_000_000.0,
    })
    mapping = {"fichas": {"CLIENTE_1": "javier curzel"}}
    r = at.ejecutar("volumen_operado",
                    {"desde": "2026-06-01", "hasta": "2026-06-30",
                     "por": "operacion", "ficha_cuenta": "CLIENTE_1"}, mapping=mapping)
    assert capturado["denominacion"] == "CURZEL, JAVIER"   # resuelto en perímetro
    assert "CURZEL" not in r                               # no vuelve al modelo
    assert "5.0 millones" in r


def test_consolidado_cuenta_que_es_operador_corrige(monkeypatch):
    import api.services.copiloto.navegacion as nv
    monkeypatch.setattr(nv, "_cuentas", lambda: [])
    monkeypatch.setattr(nv, "_operadores", lambda: [("jc@aca.com", "Javier Curzel")])
    r = at.ejecutar("volumen_operado",
                    {"desde": "2026-06-01", "hasta": "2026-06-30", "por": "mercado",
                     "ficha_cuenta": "CLIENTE_1"},
                    mapping={"fichas": {"CLIENTE_1": "javier curzel"}})
    assert "OPERADOR" in r and "ficha_operador" in r


def test_consolidado_filtro_por_ficha_operador(monkeypatch):
    capturado = _mock_consolidado(monkeypatch, {
        "metrica": "bruto", "por": "mercado", "desde": "2026-07-01",
        "hasta": "2026-07-21", "moneda": "ARS",
        "filas": [{"clave": "BYMA", "valor": 1_000_000.0, "n": 5}],
        "total": 1_000_000.0,
    })
    mapping = pg._mapping_nuevo()
    ficha = pg.asignar_ficha(mapping, "OPERADOR", "Martin Operetti")
    at.ejecutar("volumen_operado",
                {"desde": "2026-07-01", "hasta": "2026-07-21", "por": "mercado",
                 "ficha_operador": ficha}, mapping=mapping)
    assert capturado["operador_sel"] == "Martin Operetti"  # resuelto en perímetro


def test_consolidado_sin_filas(monkeypatch):
    _mock_consolidado(monkeypatch, {
        "metrica": "bruto", "por": "segmento", "desde": "2026-01-01",
        "hasta": "2026-01-02", "moneda": "ARS", "filas": [], "total": 0.0,
    })
    r = at.ejecutar("volumen_operado",
                    {"desde": "2026-01-01", "hasta": "2026-01-02", "por": "segmento"},
                    mapping={"fichas": {}})
    assert "sin operaciones" in r
