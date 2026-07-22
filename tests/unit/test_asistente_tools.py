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


def test_contrato_schemas_handlers():
    """El test que hace ESCALABLE sumar una tool: declarar el schema y olvidar
    el handler (o al revés) es un 'herramienta desconocida' en producción, y el
    modelo no lo reporta — improvisa. Acá revienta al agregarla, no en el chat.
    Reemplaza a la lista de nombres escrita a mano, que había que editar dos
    veces por tool."""
    assert set(at._HANDLERS) == at.herramientas_declaradas()


def test_toda_tool_tiene_sonda_en_el_smoke():
    """Los unit tests MOCKEAN los services: si una tool lee una clave que el
    service no emite, el test pasa en verde y la tool queda muda en producción
    (pasó 3 veces el 2026-07-22). El único que lo detecta es
    `scripts.smoke_asistente --tools`, que las corre contra la DB real — así
    que una tool nueva sin sonda ahí es una tool sin verificar de verdad.
    Declararla como None (necesita ficha de un chat) cuenta como declarada."""
    from scripts.smoke_asistente import _SONDAS
    # las claves admiten "tool#variante" para sondear varias lentes de una tool
    assert at.herramientas_declaradas() <= {k.split("#")[0] for k in _SONDAS}


def test_dimensiones_derivadas_del_sql():
    """El enum que ve el modelo sale del SQL, no de una lista paralela: si
    divergen, el modelo pide una dimensión que la jaula rechaza."""
    from api.services.operaciones_sql import dimensiones_consolidado
    vo = next(t for t in at.TOOLS if t["function"]["name"] == "volumen_operado")
    assert set(vo["function"]["parameters"]["properties"]["por"]["enum"]) == \
        set(dimensiones_consolidado())


def test_schemas_declarados():
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


# ── Tanda 1: rankings (Patrón 2) y el dominio PLATA (Patrón 3) ─────────────

def test_ranking_de_clientes_ficha_los_nombres(monkeypatch):
    """La pregunta #1 del negocio, que era estructuralmente incontestable:
    '¿quiénes son los 10 que más operaron?'. Los NOMBRES se fichan."""
    _mock_consolidado(monkeypatch, {
        "metrica": "bruto", "por": "cliente", "desde": "2026-07-01",
        "hasta": "2026-07-31", "moneda": "ARS",
        "filas": [{"clave": "MOLLO, NICOLAS EZEQUIEL", "valor": 900.0, "n": 5},
                  {"clave": "(sin)", "valor": 100.0, "n": 1}],
        "total": 1000.0,
    })
    mapping = pg._mapping_nuevo()
    r = at.ejecutar("volumen_operado",
                    {"desde": "2026-07-01", "hasta": "2026-07-31", "por": "cliente"},
                    mapping=mapping)
    assert "MOLLO" not in r and "CLIENTE_1" in r
    assert "(sin)" in r                      # el hueco no es una identidad
    assert mapping["fichas"]["CLIENTE_1"] == "MOLLO, NICOLAS EZEQUIEL"


def test_serie_mensual_ordena_cronologicamente(monkeypatch):
    capturado = _mock_consolidado(monkeypatch, {
        "metrica": "bruto", "por": "mes", "desde": "2026-01-01",
        "hasta": "2026-07-31", "moneda": "ARS",
        "filas": [{"clave": "2026-06", "valor": 100.0, "n": 2},
                  {"clave": "2026-07", "valor": 200.0, "n": 3}],
        "total": 300.0})
    r = at.ejecutar("volumen_operado", {"desde": "2026-01-01", "hasta": "2026-07-31",
                                        "por": "mes"}, mapping={"fichas": {}})
    assert capturado["por"] == "mes"
    # ojo: el encabezado trae el rango de fechas → se mira SOLO el cuerpo
    filas = [ln for ln in r.splitlines() if ln.startswith("  - ")]
    assert filas[0].startswith("  - 2026-06") and filas[1].startswith("  - 2026-07")


def test_posiciones_cuenta_da_composicion_y_concentracion(monkeypatch):
    import api.services.copiloto.navegacion as nv
    import api.services.valuaciones_sql as vs
    monkeypatch.setattr(nv, "_cuentas", lambda: [("805", "PEREZ, JUAN")])
    monkeypatch.setattr(nv, "_operadores", lambda: [])
    monkeypatch.setattr(vs, "posiciones_actuales", lambda **kw: {
        "fecha": "2026-07-21",
        "posiciones": [
            {"ticker": "AL30", "cartera": "HD", "valuacion": 700.0},
            {"ticker": "TX26", "cartera": "ARS", "valuacion": 300.0},
        ]})
    mapping = {"fichas": {"CLIENTE_1": "juan perez"}, "valores": {}, "contadores": {}}
    r = at.ejecutar("posiciones_cuenta", {"ficha_cuenta": "CLIENTE_1"}, mapping=mapping)
    assert "AL30" in r and "70.0%" in r
    assert "CONCENTRACIÓN" in r
    assert "PEREZ" not in r          # el nombre real no vuelve al modelo


def test_aum_composicion_descarta_las_cuentas(monkeypatch):
    """PII: total_snapshot trae `cuenta` e `id_cuenta` por fila — la tool
    agrega por cartera y NO puede emitir esos campos.

    El mock usa la clave REAL del service (`docs`): la versión anterior
    mockeaba `rows`, una clave inventada, así que el test pasaba en verde
    mientras la tool en producción nunca encontraba nada."""
    import api.services.portfolio_sql as ps
    monkeypatch.setattr(at, "_fecha_snapshot", lambda: "2026-07-21")
    monkeypatch.setattr(ps, "total_snapshot", lambda **kw: {"docs": [
        {"cartera": "HD", "valuacion": 600.0, "cuenta": "[805] PEREZ, JUAN", "id_cuenta": "805"},
        {"cartera": "FCI", "valuacion": 400.0, "cuenta": "[9] OTRO", "id_cuenta": "9"},
    ]})
    r = at.ejecutar("aum_composicion", {}, mapping={"fichas": {}})
    assert "HD" in r and "60.0%" in r
    assert "PEREZ" not in r and "805" not in r


def test_aum_variacion_dice_por_quien_se_movio(monkeypatch):
    """'El AuM subió 8%' no es una respuesta: la pregunta real es POR QUIÉN.
    Mock con el shape REAL de portfolio_sql.total_diff."""
    import api.services.portfolio_sql as ps
    monkeypatch.setattr(ps, "total_diff", lambda **kw: {
        "fecha_actual_resuelta": "2026-07-21", "fecha_anterior_resuelta": "2026-06-30",
        "moneda": kw["moneda"], "mep_missing_actual": False, "mep_missing_anterior": False,
        "filas": [
            {"id_cuenta": "805", "cuenta": "[805] PEREZ, JUAN", "saldo_actual": 900.0,
             "saldo_anterior": 100.0, "diff": 800.0, "es_nueva": False, "es_cerrada": False},
            {"id_cuenta": "9", "cuenta": "[9] GOMEZ, ANA", "saldo_actual": None,
             "saldo_anterior": 50.0, "diff": -50.0, "es_nueva": False, "es_cerrada": True},
        ],
        "total_diff": 750.0, "n_total": 2, "n_nuevas": 0, "n_cerradas": 1})
    mapping = pg._mapping_nuevo()
    r = at.ejecutar("aum_variacion", {"desde": "2026-06-30", "hasta": "2026-07-21"},
                    mapping=mapping)
    assert "SUMARON" in r and "RESTARON" in r and "(se fue)" in r
    assert "PEREZ" not in r and "GOMEZ" not in r and "CLIENTE_1" in r
    assert "CONCENTRACIÓN" in r


def test_aum_variacion_sin_tipo_de_cambio_no_inventa(monkeypatch):
    import api.services.portfolio_sql as ps
    monkeypatch.setattr(ps, "total_diff", lambda **kw: {
        "filas": [{"id_cuenta": "1", "cuenta": "x", "diff": 1.0,
                   "es_nueva": False, "es_cerrada": False}],
        "mep_missing_actual": True, "mep_missing_anterior": False})
    r = at.ejecutar("aum_variacion", {"hasta": "2026-07-21", "moneda": "USD"},
                    mapping={"fichas": {}})
    assert "falta el tipo de cambio" in r


def test_cobros_futuros_resume_y_marca_el_pico(monkeypatch):
    import api.services.cashflow_sql as cf
    monkeypatch.setattr(cf, "por_dia", lambda **kw: [
        {"fecha": "2026-08-01", "por_moneda": {"ARS": 100.0}, "n_clientes": 3, "n_pagos": 4},
        {"fecha": "2026-08-09", "por_moneda": {"ARS": 900.0}, "n_clientes": 7, "n_pagos": 9},
    ])
    r = at.ejecutar("cobros_futuros", {"dias": 30}, mapping={"fichas": {}})
    assert "día pico: 2026-08-09" in r
    assert "TOTAL" in r and "1000 ARS" in r


def test_pulso_mesa_respeta_el_permiso_por_usuario(monkeypatch):
    """El gate que la auditoría marcó como fuga: la web protege esto con un
    permiso POR USUARIO que NO es el rol. Sin el flag, ni un número."""
    import api.services.control_comercial_sql as cc
    llamadas = []
    # shape REAL de control_comercial_sql.datos_totales_alyc (verificado):
    # {moneda, ancla, filas:[{periodo, clientes_activos, volumen, comisiones,
    #                         <campo>_pct}]}
    monkeypatch.setattr(cc, "datos_totales_alyc",
                        lambda **kw: llamadas.append(1) or {
                            "moneda": "ARS", "ancla": "2026-07-21",
                            "filas": [{"periodo": "Mes", "volumen": 1_000_000.0,
                                       "volumen_pct": 12.5, "comisiones": 50_000.0,
                                       "clientes_activos": 33}]})

    monkeypatch.setattr(at, "puede_control_comercial", lambda u: False)
    r = at.ejecutar("pulso_mesa", {}, mapping={"fichas": {}}, usuario="sin@flag.com")
    assert "permiso" in r and not llamadas          # ni siquiera consultó

    monkeypatch.setattr(at, "puede_control_comercial", lambda u: True)
    r2 = at.ejecutar("pulso_mesa", {}, mapping={"fichas": {}}, usuario="jefe@x.com")
    assert "Mes" in r2 and "+12.5%" in r2
    assert "cuentas activas 33" in r2


def test_flujo_de_fondos_separa_mercado_de_plata_nueva(monkeypatch):
    """La pregunta que el AuM no contesta: si subió, ¿es aporte o valorización?
    Agrega por MONEDA; las cuentas de las filas no salen del perímetro."""
    import api.services.cashflow_sql as cf
    monkeypatch.setattr(cf, "flujos_resumen", lambda **kw: {"filas": [
        {"dia": "2026-07-01", "cuenta": "[805] PEREZ, JUAN", "unidad": "ARS",
         "entradas": 1_000.0, "salidas": -400.0, "n": 3},
        {"dia": "2026-07-02", "cuenta": "[9] OTRO", "unidad": "ARS",
         "entradas": 500.0, "salidas": 0.0, "n": 1},
    ]})
    r = at.ejecutar("flujo_de_fondos", {"desde": "2026-07-01", "hasta": "2026-07-31"},
                    mapping={"fichas": {}})
    assert "NETO 1100 ARS" in r and "4 movimientos" in r
    assert "PEREZ" not in r and "805" not in r


def test_flujo_de_fondos_sin_movimientos(monkeypatch):
    import api.services.cashflow_sql as cf
    monkeypatch.setattr(cf, "flujos_resumen", lambda **kw: {"filas": []})
    r = at.ejecutar("flujo_de_fondos", {"desde": "2019-01-01", "hasta": "2019-01-31"},
                    mapping={"fichas": {}})
    assert "no hay movimientos" in r


def test_jobs_fallidos_resume_lo_roto(monkeypatch):
    import api.services.manager_infra_sql as mi
    monkeypatch.setattr(mi, "jobs_history_stats_sql", lambda d: [
        {"tipo": "aranceles", "total": 7, "error": 3, "last_run": "21/07 20:00",
         "last_status": "error"},
        {"tipo": "bcra", "total": 7, "error": 0, "last_run": "21/07 22:00",
         "last_status": "ok"},
    ])
    r = at.ejecutar("jobs_fallidos", {"dias": 7}, mapping={"fichas": {}})
    assert "aranceles" in r and "3 errores" in r
    assert "bcra" not in r          # lo que anda no ensucia la respuesta


def test_jobs_fallidos_todo_ok(monkeypatch):
    import api.services.manager_infra_sql as mi
    monkeypatch.setattr(mi, "jobs_history_stats_sql", lambda d: [
        {"tipo": "bcra", "total": 7, "error": 0, "last_run": "x", "last_status": "ok"}])
    assert "todo OK" in at.ejecutar("jobs_fallidos", {}, mapping={"fichas": {}})


def test_costo_ia_reporta_por_proveedor(monkeypatch):
    import api.services.ia_obs as obs
    monkeypatch.setattr(obs, "observabilidad", lambda **kw: {
        "hoy": {"tokens_total": 500_000, "presupuesto_pct": 25.0, "llamadas": 40,
                "errores": 0},
        "por_proveedor": [
            {"proveedor": "openai", "costo_usd": 0.42, "costo_usd_hoy": 0.11,
             "llamadas": 12, "costo_estimable": True, "no_entrena": True},
            {"proveedor": "raro", "costo_estimable": False},
        ]})
    r = at.ejecutar("costo_ia", {}, mapping={"fichas": {}})
    assert "openai" in r and "0.4200" in r and "no entrena" in r
    assert "raro" not in r          # sin precio no se inventa un costo


def test_controles_calidad_solo_cuenta_no_expone_casos(monkeypatch):
    """PII: dos controles traen denominaciones. La tool devuelve el CONTEO.

    Mock con la clave REAL (`controles`): mockeando el dict plano el test
    pasaba mientras la tool en producción decía "todo limpio" siempre."""
    import api.services.controles_sql as cs
    monkeypatch.setattr(cs, "listar_controles", lambda **kw: {
        "controles": {
            "comitentes_sin_nivel1": {"activos": [
                {"item": "805", "detalle": "PEREZ, JUAN sin nivel 1",
                 "desde": "2026-07-01"}], "resueltos": []},
            "todo_bien": {"activos": [], "resueltos": []},
        },
        "totales": {"comitentes_sin_nivel1": 1, "todo_bien": 0},
        "ultima_corrida": "2026-07-21 20:00:00",
    })
    r = at.ejecutar("controles_calidad_datos", {}, mapping={"fichas": {}})
    assert "comitentes_sin_nivel1: 1 casos" in r
    assert "PEREZ" not in r and "805" not in r
    assert "todo_bien" not in r


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
