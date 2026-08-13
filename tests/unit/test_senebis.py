"""SENEBIS — unit de la lógica pura (derivados, defaults, inferencia, export).

Sin DB: el export se testea monkeypatcheando `listar_ops` con filas fijas y
releyendo el .xlsx generado con openpyxl.
"""
from __future__ import annotations

from io import BytesIO

import pytest

from api.services import senebis as svc

# ── monto = vn × px / 100 (px cada 100 VN) ───────────────────────────────────

def test_monto_derivado_formula_planilla():
    # Fila real de la planilla: SXC5O — VN 200.000.000 × PX 105,110 → 210.220.000
    assert svc._derivar_monto({"vn": 200_000_000, "px": 105.110}) == pytest.approx(210_220_000)


def test_monto_override_manual_gana():
    assert svc._derivar_monto({"vn": 100, "px": 50, "monto": 999}) == 999


def test_monto_none_si_falta_pata():
    assert svc._derivar_monto({"vn": 100}) is None
    assert svc._derivar_monto({"px": 50}) is None
    assert svc._derivar_monto({}) is None


# ── plazo ↔ liquidacion (inferencia con día hábil) ───────────────────────────

def test_plazo_ci_liquida_mismo_dia():
    # 2026-08-04 es martes (hábil).
    f = svc._completar_fechas({"concertacion": "2026-08-04", "plazo": "CI"})
    assert f == {"concertacion": "2026-08-04", "liquidacion": "2026-08-04", "plazo": "CI"}


def test_plazo_24_liquida_proximo_habil():
    f = svc._completar_fechas({"concertacion": "2026-08-04", "plazo": "24hs"})
    assert f["plazo"] == "24"
    assert f["liquidacion"] == "2026-08-05"


def test_plazo_24_salta_finde():
    # Viernes 2026-08-07 + 24hs → lunes 2026-08-10.
    f = svc._completar_fechas({"concertacion": "2026-08-07", "plazo": "24"})
    assert f["liquidacion"] == "2026-08-10"


def test_liquidacion_infiere_plazo():
    mismo = svc._completar_fechas({"concertacion": "2026-08-04", "liquidacion": "2026-08-04"})
    assert mismo["plazo"] == "CI"
    dia_sig = svc._completar_fechas({"concertacion": "2026-08-04", "liquidacion": "2026-08-05"})
    assert dia_sig["plazo"] == "24"


def test_liquidacion_anterior_rechazada():
    with pytest.raises(ValueError):
        svc._completar_fechas({"concertacion": "2026-08-04", "liquidacion": "2026-08-03"})


def test_plazo_invalido_rechazado():
    with pytest.raises(ValueError):
        svc._norm_plazo("48")


def test_sin_fechas_default_hoy_ci():
    f = svc._completar_fechas({})
    assert f["plazo"] == "CI"
    assert f["concertacion"] == f["liquidacion"]  # CI liquida el mismo día


# ── validación + normalización ───────────────────────────────────────────────

def _payload_ok() -> dict:
    return {"operacion": "compra", "concertacion": "2026-08-04", "especie": "tzxm7"}


def test_validar_ok_case_insensitive():
    svc._validar(_payload_ok())  # no levanta


@pytest.mark.parametrize("campo,valor", [
    ("operacion", "PASE"), ("operacion", ""), ("especie", "  "),
])
def test_validar_rechaza(campo, valor):
    p = _payload_ok()
    p[campo] = valor
    with pytest.raises(ValueError):
        svc._validar(p)


def test_validar_externo_exige_agente():
    with pytest.raises(ValueError):
        svc._validar({**_payload_ok(), "tipo_contraparte": "externo"})


def test_row_normaliza_y_defaults(monkeypatch):
    monkeypatch.setattr(svc, "_buscar_cuenta_exacta", lambda term: None)
    row = svc._row_de_payload(
        {**_payload_ok(), "vn": 1000, "px": 50, "mercado": "no garantizado"},
        actor="Trader@Acaquant.com")
    assert row["operacion"] == "COMPRA"
    assert row["especie"] == "TZXM7"
    assert row["mercado"] == "NO GARANTIZADO"
    assert row["monto"] == pytest.approx(500)
    assert row["cp"] == "255"          # default cartera propia
    assert row["plazo"] == "CI"        # default sin plazo ni liquidacion
    assert row["tipo_contraparte"] == "interno"  # default
    assert row["por"] == "trader@acaquant.com"


# ── contraparte: interno (resolución de cuenta) y externo (agente) ───────────

def test_denominacion_limpia():
    assert svc._denominacion_limpia("[805] ACME SA") == "ACME SA"
    assert svc._denominacion_limpia("ACME SA") == "ACME SA"
    assert svc._denominacion_limpia("  ") is None


def test_resolver_interno_matchea_y_snapshotea(monkeypatch):
    monkeypatch.setattr(
        svc, "_buscar_cuenta_exacta",
        lambda term: {"id_cuenta": "805", "denominacion": "ACME SA"}
        if term in ("805", "ACME SA") else None)
    assert svc._resolver_interno("805") == ("805", "ACME SA")
    assert svc._resolver_interno("ACME SA") == ("805", "ACME SA")   # por nombre → número
    assert svc._resolver_interno("BYMA") == ("BYMA", None)          # sin match → tal cual
    assert svc._resolver_interno("") == (None, None)


def test_row_externo_snapshotea_numero_de_agente(monkeypatch):
    monkeypatch.setattr(svc, "_numero_agente",
                        lambda n: "733" if n == "COCOS" else None)
    row = svc._row_de_payload(
        {**_payload_ok(), "tipo_contraparte": "externo", "agente": "cocos",
         "cc": "219"},
        actor="t@x.com")
    assert row["agente"] == "COCOS"
    assert row["agente_numero"] == "733"
    assert row["cc"] is None            # externo: la cc no aplica
    with pytest.raises(ValueError):     # agente fuera del catálogo → error claro
        svc._row_de_payload(
            {**_payload_ok(), "tipo_contraparte": "externo", "agente": "OTRO"},
            actor="t@x.com")


# ── export .xlsx (formato del sistema destino) ───────────────────────────────

_FILA = {
    "id": 13629, "operacion": "COMPRA", "concertacion": "2026-08-04",
    "liquidacion": "2026-08-05", "plazo": "24", "especie": "TZXM7",
    "vn": 500_000_000.0, "px": 217.2, "monto": 1_086_000_000.0,
    "cp": "255", "cc": "219", "cc_denominacion": None,
    "contraparte": None, "nro_contraparte": None,
    "mercado": "NO GARANTIZADO", "cargan_ellos": False, "tipo": None,
    "tipo_contraparte": "interno", "agente": None, "agente_numero": None,
    "es_mae": False,
    "estado": "pendiente", "completada_por": None, "completada_at": None,
    "creado_por": "t@x.com", "creado_at": None, "actualizado_por": None,
}


def test_excel_solo_pendientes(monkeypatch):
    # El archivo se sube varias veces por día: lo COMPLETADO ya está cargado
    # en Quantex → re-exportarlo lo duplicaría. Solo pendientes no-MAE.
    pendiente = {**_FILA, "id": 1}
    completada = {**_FILA, "id": 2, "estado": "completada"}
    monkeypatch.setattr(svc, "listar_ops",
                        lambda **kw: {"ordenes": [pendiente, completada], "conectados": []})
    monkeypatch.setattr(svc, "proximo_id", lambda: 3)
    prev = svc.excel_preview()
    assert [f["id"] for f in prev["filas"]] == [1]


def test_mae_fija_tipo_y_queda_fuera_del_excel(monkeypatch):
    monkeypatch.setattr(svc, "_buscar_cuenta_exacta", lambda term: None)
    row = svc._row_de_payload(
        {**_payload_ok(), "es_mae": True, "tipo": "pasada"}, actor="t@x.com")
    assert row["es_mae"] is True
    assert row["tipo"] == "MAE"          # automático, pisa lo tipeado

    mae = {**_FILA, "id": 1, "es_mae": True}
    normal = {**_FILA, "id": 2}
    monkeypatch.setattr(svc, "listar_ops",
                        lambda **kw: {"ordenes": [mae, normal], "conectados": []})
    monkeypatch.setattr(svc, "proximo_id", lambda: 3)
    prev = svc.excel_preview()
    assert [f["id"] for f in prev["filas"]] == [2]   # la MAE no aparece


def test_cargan_ellos_es_si_no_y_queda_fuera_del_excel(monkeypatch):
    # SI/NO desde el form nuevo (bool) + tolerancia al payload viejo (texto).
    monkeypatch.setattr(svc, "_buscar_cuenta_exacta", lambda term: None)
    row = svc._row_de_payload({**_payload_ok(), "cargan_ellos": True}, actor="t@x.com")
    assert row["cargan_ellos"] is True
    row = svc._row_de_payload({**_payload_ok(), "cargan_ellos": "pasada"}, actor="t@x.com")
    assert row["cargan_ellos"] is True   # texto anotado = cargaban ellos
    row = svc._row_de_payload({**_payload_ok(), "cargan_ellos": "no"}, actor="t@x.com")
    assert row["cargan_ellos"] is False
    row = svc._row_de_payload(_payload_ok(), actor="t@x.com")
    assert row["cargan_ellos"] is False  # default NO

    # Con SI, la orden NO entra al Excel/espejo (la carga la contraparte).
    cargan = {**_FILA, "id": 1, "cargan_ellos": True}
    normal = {**_FILA, "id": 2}
    monkeypatch.setattr(svc, "listar_ops",
                        lambda **kw: {"ordenes": [cargan, normal], "conectados": []})
    monkeypatch.setattr(svc, "proximo_id", lambda: 3)
    prev = svc.excel_preview()
    assert [f["id"] for f in prev["filas"]] == [2]


def test_export_xlsx_columnas_y_valores(monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    monkeypatch.setattr(svc, "listar_ops", lambda **kw: {"ordenes": [_FILA]})
    contenido, nombre = svc.export_xlsx()
    assert nombre.startswith("senebis_") and nombre.endswith(".xlsx")

    ws = openpyxl.load_workbook(BytesIO(contenido)).active
    headers = [c.value for c in ws[1]]
    assert headers == ["ID", "OPERACION", "INSTRUMENTO", "PLAZO", "PRECIO",
                       "CANTIDAD", "CONTRAPARTE", "COMITENTE", "CARTERA PROPIA",
                       "MERCADO"]
    fila = [c.value for c in ws[2]]
    assert fila[0] == 13629                       # ID = secuencia global de la tabla
    assert fila[1] == "COMPRA"
    assert fila[2] == "TZXM7"
    assert fila[3] == "24"
    assert fila[4] == pytest.approx(217.2)
    assert fila[5] == pytest.approx(500_000_000)
    assert fila[6] is None                        # interno → CONTRAPARTE vacío
    assert fila[7] == 219                         # COMITENTE = cc, numérico
    assert fila[8] == 255                         # CARTERA PROPIA ídem
    assert fila[9] == "NO GARANTIZADO"


def test_export_reglas_contraparte():
    # interno NO GARANTIZADO → COMITENTE = cc
    fila = svc._fila_export(_FILA)
    assert fila[6] is None and fila[7] == 219
    # interno GARANTIZADO → COMITENTE = cp (la 255)
    fila = svc._fila_export({**_FILA, "mercado": "GARANTIZADO"})
    assert fila[6] is None and fila[7] == 255
    # externo → COMITENTE vacío, CONTRAPARTE = número del agente
    fila = svc._fila_export({**_FILA, "tipo_contraparte": "externo",
                             "agente": "COCOS", "agente_numero": "733", "cc": None})
    assert fila[6] == 733 and fila[7] is None


def test_excel_preview_mismas_reglas(monkeypatch):
    monkeypatch.setattr(svc, "listar_ops",
                        lambda **kw: {"ordenes": [_FILA], "conectados": []})
    monkeypatch.setattr(svc, "proximo_id", lambda: 13646)
    prev = svc.excel_preview()
    assert prev["proximo_id"] == 13646
    assert prev["headers"][0] == "ID"
    fila = prev["filas"][0]
    assert fila["id"] == 13629 and fila["estado"] == "pendiente"
    assert fila["valores"] == svc._fila_export(_FILA)


def test_set_proximo_id_no_retrocede(monkeypatch):
    # Con 13640 ya cargado, fijar 13640 (o menos) rompería el próximo insert.
    monkeypatch.setattr(svc, "_q", lambda sql, params=None: [{"m": 13640}])
    with pytest.raises(ValueError):
        svc.set_proximo_id(13640, actor="a@x.com")


def test_export_xlsx_comitente_texto_queda_texto(monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    fila = {**_FILA, "cc": "lombard ab plus por mae 733"}
    monkeypatch.setattr(svc, "listar_ops", lambda **kw: {"ordenes": [fila]})
    contenido, _ = svc.export_xlsx()
    ws = openpyxl.load_workbook(BytesIO(contenido)).active
    assert ws.cell(row=2, column=8).value == "lombard ab plus por mae 733"


def test_export_xlsx_orden_por_id(monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    a, b = {**_FILA, "id": 13629}, {**_FILA, "id": 13618}
    monkeypatch.setattr(svc, "listar_ops", lambda **kw: {"ordenes": [a, b]})
    contenido, _ = svc.export_xlsx()
    ws = openpyxl.load_workbook(BytesIO(contenido)).active
    assert ws.cell(row=2, column=1).value == 13618
    assert ws.cell(row=3, column=1).value == 13629


# ── destino MAE (catálogos para el futuro Excel MAE) ─────────────────────────

def _sin_db(monkeypatch, q_rows: list | None = None) -> dict:
    """Captura los params del _exec sin tocar la DB (y silencia audit y _q)."""
    capturado: dict = {}
    monkeypatch.setattr(svc, "_exec", lambda sql, params: capturado.update(params) or 1)
    monkeypatch.setattr(svc, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_q", lambda sql, params=None: q_rows or [])
    return capturado


def test_upsert_agente_normaliza_codigo_mae(monkeypatch):
    capturado = _sin_db(monkeypatch)
    r = svc.upsert_agente("cocos", "733", actor="A@x.com", codigo_mae=" abc01 ")
    assert r == {"nombre": "COCOS", "numero": "733", "codigo_mae": "ABC01"}
    assert capturado["cod"] == "ABC01"
    # Sin código → None (el COALESCE del SQL preserva el ya cargado).
    r = svc.upsert_agente("cocos", "733", actor="a@x.com")
    assert r["codigo_mae"] is None


def test_update_contraparte_codigo_mae(monkeypatch):
    # El DESTINO MAE de cuentas internas vive en clientes.contrapartes:
    # PATCH normaliza a MAYÚSCULAS, "" borra el código y None no lo toca.
    from api.services import contrapartes_seg as cseg
    capturado: dict = {}
    monkeypatch.setattr(cseg, "_exec", lambda sql, params: capturado.update(params) or 1)
    monkeypatch.setattr(cseg, "_q", lambda sql, params=None: [
        {"cuenta": "805", "denominacion": "X", "contraparte": "FIMA",
         "segmento": "Fondos", "codigo_mae": capturado.get("codigo_mae")}])
    r = cseg.update_contraparte(cuenta="805", codigo_mae=" f062 ", actor="a@x.com")
    assert r["updated"] and capturado["codigo_mae"] == "F062"
    cseg.update_contraparte(cuenta="805", codigo_mae="", actor="a@x.com")
    assert capturado["codigo_mae"] is None          # "" = borrar
    capturado.clear()
    cseg.update_contraparte(cuenta="805", segmento="Fondos", actor="a@x.com")
    assert "codigo_mae" not in capturado            # None = no tocar


# ── estado: días anteriores bloqueados (salvo admin) ─────────────────────────

def test_set_estado_bloquea_dias_anteriores(monkeypatch):
    _sin_db(monkeypatch)
    vieja = {**_FILA, "concertacion": "2020-01-02", "estado": "pendiente"}
    monkeypatch.setattr(svc, "_get_op", lambda i: dict(vieja))
    monkeypatch.setattr(svc, "es_admin", lambda e: False)
    with pytest.raises(ValueError):
        svc.set_estado(1, "completada", actor="t@x.com")
    # Admin sí (corrección consciente, auditada).
    monkeypatch.setattr(svc, "es_admin", lambda e: True)
    assert svc.set_estado(1, "completada", actor="a@x.com")["estado"] == "pendiente"


def test_set_estado_hoy_permitido(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    _sin_db(monkeypatch)
    hoy = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).date().isoformat()
    monkeypatch.setattr(svc, "_get_op",
                        lambda i: {**_FILA, "concertacion": hoy, "estado": "pendiente"})
    monkeypatch.setattr(svc, "es_admin", lambda e: False)
    assert svc.set_estado(1, "completada", actor="t@x.com")["estado"] == "pendiente"


# ── Excel MAE (espejo/export de las órdenes es_mae) ──────────────────────────

def test_fila_export_mae_precio_unitario_y_forma():
    o = {**_FILA, "es_mae": True, "px": 105.110}
    fila = svc._fila_export_mae(o, "F062")
    # Operacion capitalizada · Instrumento · Plazo · Moneda ARS · Precio ÷100
    # (unitario) · Cantidad · Destino · Segmento (sin cargar → el default, que
    # es lo que espera el MAE: ninguna fila sale con la celda vacía).
    assert fila == ["Compra", "TZXM7", "24", "ARS", pytest.approx(1.0511),
                    500_000_000.0, "F062", svc.SEGMENTO_MAE_DEFAULT]


def test_filas_mae_solo_pendientes_mae():
    mae = {**_FILA, "id": 1, "es_mae": True}
    normal = {**_FILA, "id": 2}
    completada = {**_FILA, "id": 3, "es_mae": True, "estado": "completada"}
    cargan = {**_FILA, "id": 4, "es_mae": True, "cargan_ellos": True}
    assert [o["id"] for o in svc._filas_mae([normal, completada, cargan, mae])] == [1]


# ── vista consolidada (1 request en vez de 3) ───────────────────────────────

def test_coincide_espeja_el_where_de_listar_ops():
    o = {**_FILA, "especie": "TZXM7", "estado": "pendiente", "es_mae": False}
    assert svc._coincide(o, None, None, None)                 # sin filtros
    assert svc._coincide(o, "pendiente", None, None)
    assert not svc._coincide(o, "completada", None, None)
    assert svc._coincide(o, None, "zxm", None)                # ILIKE %x%
    assert svc._coincide(o, None, "TZXM7", None)
    assert not svc._coincide(o, None, "AL30", None)
    assert svc._coincide(o, None, None, "sin")                # mae=sin → no-MAE
    assert not svc._coincide(o, None, None, "solo")
    assert svc._coincide({**o, "es_mae": True}, None, None, "solo")


def test_vista_los_filtros_de_la_tabla_no_tocan_los_espejos(monkeypatch):
    # El archivo del Excel no puede depender de cómo el trader filtró la
    # pantalla: los espejos se arman sobre la lista COMPLETA del rango.
    quantex = {**_FILA, "id": 1, "es_mae": False}
    mae = {**_FILA, "id": 2, "es_mae": True}
    monkeypatch.setattr(svc, "listar_ops",
                        lambda **kw: {"ordenes": [quantex, mae], "conectados": []})
    monkeypatch.setattr(svc, "_destinos_mae", lambda ords: {2: "F062"})
    monkeypatch.setattr(svc, "proximo_id", lambda: 99)
    v = svc.vista(mae="solo")
    assert [o["id"] for o in v["ordenes"]] == [2]              # tabla filtrada
    assert [f["id"] for f in v["excel"]["filas"]] == [1]       # espejo COMPLETO
    assert [f["id"] for f in v["excel_mae"]["filas"]] == [2]
    assert v["proximo_id"] == 99


def test_vista_rechaza_filtros_invalidos(monkeypatch):
    monkeypatch.setattr(svc, "listar_ops", lambda **kw: {"ordenes": [], "conectados": []})
    with pytest.raises(ValueError):
        svc.vista(estado="cualquiera")
    with pytest.raises(ValueError):
        svc.vista(mae="quizas")


def test_vista_lee_una_sola_vez(monkeypatch):
    # El punto de todo el cambio: UNA lectura por ciclo, no tres.
    llamadas = []
    monkeypatch.setattr(svc, "listar_ops",
                        lambda **kw: llamadas.append(kw) or {"ordenes": [], "conectados": []})
    monkeypatch.setattr(svc, "proximo_id", lambda: 1)
    svc.vista(desde="2026-08-13", estado="pendiente", mae="solo", email="t@x.com")
    assert len(llamadas) == 1
    # …y esa lectura NO lleva los filtros de la tabla (los espejos los ignoran).
    assert llamadas[0] == {"desde": "2026-08-13", "hasta": None, "email": "t@x.com"}


# ── tilde propia de la tab EXCEL MAE (mae_completada) ───────────────────────

def test_filas_mae_tildada_sale_del_archivo_pero_queda_en_el_espejo():
    # La tilde del trader saca la orden del .xlsx (que se genera varias veces
    # por día) pero la deja visible en el espejo para poder destildarla.
    pendiente = {**_FILA, "id": 1, "es_mae": True}
    tildada = {**_FILA, "id": 2, "es_mae": True, "mae_completada": True}
    assert [o["id"] for o in svc._filas_mae([pendiente, tildada])] == [1]
    assert [o["id"] for o in
            svc._filas_mae([pendiente, tildada], incluir_completadas=True)] == [1, 2]


def test_filas_mae_tilde_es_independiente_del_estado():
    # `estado` lo mueve el BACK OFFICE (Quantex): que la orden siga pendiente
    # no la devuelve al archivo si el trader ya la cargó en el MAE.
    tildada_pendiente = {**_FILA, "id": 1, "es_mae": True, "mae_completada": True}
    assert svc._filas_mae([tildada_pendiente]) == []


def test_set_mae_completada_no_toca_estado(monkeypatch):
    capturado = _sin_db(monkeypatch)
    monkeypatch.setattr(svc, "_get_op",
                        lambda i: {**_FILA, "es_mae": True, "mae_completada": False})
    svc.set_mae_completada(1, True, actor="T@x.com")
    assert capturado["mae"] is True and capturado["mae_por"] == "t@x.com"
    assert "estado" not in capturado          # el tablero del back office no se toca


def test_set_mae_completada_destildar_limpia_quien_y_cuando(monkeypatch):
    capturado = _sin_db(monkeypatch)
    monkeypatch.setattr(svc, "_get_op",
                        lambda i: {**_FILA, "es_mae": True, "mae_completada": True})
    svc.set_mae_completada(1, False, actor="t@x.com")
    assert capturado["mae"] is False
    assert capturado["mae_por"] is None and capturado["mae_at"] is None


def test_destildar_baja_el_amarillo_del_mae(monkeypatch):
    # Destildar devuelve la orden al Excel → se re-carga con los datos buenos
    # y el aviso "el MAE quedó viejo" deja de aplicar. Lo hace el propio UPDATE
    # (AND con el valor nuevo): tildar no lo revive, solo una edición posterior.
    sqls: list[str] = []
    monkeypatch.setattr(svc, "_exec", lambda sql, params: sqls.append(sql) or 1)
    monkeypatch.setattr(svc, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_get_op", lambda i: {
        **_FILA, "es_mae": True, "mae_completada": True,
        "mae_editada_completada": True})
    svc.set_mae_completada(1, False, actor="t@x.com")
    assert "mae_editada_completada=(mae_editada_completada AND %(mae)s)" in sqls[0]


def test_set_mae_completada_rechaza_no_mae(monkeypatch):
    _sin_db(monkeypatch)
    monkeypatch.setattr(svc, "_get_op", lambda i: {**_FILA, "es_mae": False})
    with pytest.raises(ValueError):
        svc.set_mae_completada(1, True, actor="t@x.com")


def test_set_mae_completada_idempotente(monkeypatch):
    capturado = _sin_db(monkeypatch)
    monkeypatch.setattr(svc, "_get_op",
                        lambda i: {**_FILA, "es_mae": True, "mae_completada": True})
    svc.set_mae_completada(1, True, actor="t@x.com")
    assert capturado == {}                    # no escribe ni audita de nuevo


def test_marcar_edicion_prende_el_amarillo_del_mae(monkeypatch):
    # Editar una orden YA TILDADA = el MAE quedó con los datos viejos (espejo
    # de editada_completada, que mira `estado`).
    capturado = _sin_db(monkeypatch)
    monkeypatch.setattr(svc, "_get_op", lambda i: {**_FILA, "es_mae": True})
    before = {**_FILA, "es_mae": True, "mae_completada": True,
              "campos_editados": [], "editada_completada": False,
              "mae_editada_completada": False}
    svc._marcar_edicion(1, before, {**before, "px": 999.0})
    assert capturado["flag_mae"] is True
    # …y la de Quantex NO se prende sola: esa mira el estado, que sigue pendiente.
    assert capturado["flag"] is False


def test_marcar_edicion_sin_tilde_no_prende_nada(monkeypatch):
    capturado = _sin_db(monkeypatch)
    monkeypatch.setattr(svc, "_get_op", lambda i: {**_FILA, "es_mae": True})
    before = {**_FILA, "es_mae": True, "mae_completada": False,
              "campos_editados": [], "editada_completada": False,
              "mae_editada_completada": False}
    svc._marcar_edicion(1, before, {**before, "px": 999.0})
    assert capturado["flag_mae"] is False


def test_visto_de_quantex_no_baja_el_amarillo_del_mae(monkeypatch):
    # Dos marcas y dos vistos: las bajan equipos distintos y no pueden taparse.
    capturado = _sin_db(monkeypatch)
    monkeypatch.setattr(svc, "_get_op", lambda i: {
        **_FILA, "campos_editados": ["px"], "editada_completada": True,
        "mae_editada_completada": True})
    svc.limpiar_marcas(1, actor="b@x.com")
    assert "mae_editada_completada" not in capturado
    capturado.clear()
    svc.limpiar_marcas_mae(1, actor="t@x.com")
    assert capturado == {"id": 1}          # solo baja SU marca


def test_set_mae_completada_no_bloquea_dias_anteriores(monkeypatch):
    # A diferencia de set_estado: una orden vieja sin tildar sigue entrando al
    # archivo, y sacarla de ahí es justamente para lo que existe la tilde.
    capturado = _sin_db(monkeypatch)
    monkeypatch.setattr(svc, "_get_op", lambda i: {
        **_FILA, "concertacion": "2020-01-02", "es_mae": True, "mae_completada": False})
    monkeypatch.setattr(svc, "es_admin", lambda e: False)
    svc.set_mae_completada(1, True, actor="t@x.com")
    assert capturado["mae"] is True


def test_destino_con_letra_automatica():
    # El usuario carga SOLO el número: la letra la pone el sistema según la
    # orden — interno → F (fondo), externo → A (agente).
    assert svc._destino_con_letra("062", "interno") == "F062"
    assert svc._destino_con_letra("733", "externo") == "A733"
    # Si lo cargado ya trae letra (C+CUIT, SXXX), se respeta tal cual.
    assert svc._destino_con_letra("S010", "interno") == "S010"
    assert svc._destino_con_letra(None, "interno") is None
    assert svc._destino_con_letra("  ", "externo") is None


def test_destinos_mae_resuelve_por_tipo(monkeypatch):
    # En la base vive solo el NÚMERO; acá sale con la letra ya puesta.
    def _q_fake(sql, params=None):
        if "clientes.contrapartes" in sql:
            return [{"id_cuenta": "219", "codigo_mae": "062"}]
        return [{"nombre": "COCOS", "codigo_mae": "733"}]
    monkeypatch.setattr(svc, "_q", _q_fake)
    interno = {**_FILA, "id": 1, "es_mae": True}                     # cc=219
    externo = {**_FILA, "id": 2, "es_mae": True,
               "tipo_contraparte": "externo", "agente": "COCOS", "cc": None}
    sin_codigo = {**_FILA, "id": 3, "es_mae": True, "cc": "999"}
    d = svc._destinos_mae([interno, externo, sin_codigo])
    assert d == {1: "F062", 2: "A733", 3: None}


def test_excel_mae_preview_marca_sin_destino(monkeypatch):
    mae_ok = {**_FILA, "id": 1, "es_mae": True}
    mae_sin = {**_FILA, "id": 2, "es_mae": True, "cc": "999"}
    normal = {**_FILA, "id": 3}
    monkeypatch.setattr(svc, "listar_ops",
                        lambda **kw: {"ordenes": [mae_ok, mae_sin, normal],
                                      "conectados": []})
    monkeypatch.setattr(svc, "_destinos_mae", lambda ords: {1: "F062", 2: None})
    prev = svc.excel_mae_preview()
    assert prev["headers"] == ["Operacion", "Instrumento", "Plazo", "Moneda",
                               "Precio", "Cantidad", "Destino", "Segmento"]
    assert [f["id"] for f in prev["filas"]] == [1, 2]   # la no-MAE afuera
    assert prev["filas"][0]["sin_destino"] is False
    assert prev["filas"][1]["sin_destino"] is True
    assert prev["filas"][1]["valores"][6] is None       # DESTINO vacío


def test_export_mae_xlsx(monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    from io import BytesIO
    mae = {**_FILA, "id": 1, "es_mae": True, "px": 346.1}
    monkeypatch.setattr(svc, "listar_ops", lambda **kw: {"ordenes": [mae]})
    monkeypatch.setattr(svc, "_destinos_mae", lambda ords: {1: "F700"})
    contenido, nombre = svc.export_mae_xlsx()
    assert nombre.startswith("senebis_mae_") and nombre.endswith(".xlsx")
    ws = openpyxl.load_workbook(BytesIO(contenido)).active
    assert [c.value for c in ws[1]] == list(svc._HEADERS_MAE)
    fila = [c.value for c in ws[2]]
    assert fila[0] == "Compra" and fila[3] == "ARS"
    assert fila[4] == pytest.approx(3.461)              # px unitario
    assert fila[6] == "F700"


# ── marcas de edición (el amarillo que se pintaba a mano) ───────────────────

def test_diff_campos_detecta_lo_tocado():
    after = {**_FILA, "px": 218.0, "vn": 400_000_000.0}
    assert svc._diff_campos(_FILA, after) == ["vn", "px"]


def test_diff_campos_ignora_metadata():
    # actualizado_por/creado_at cambian en cada edición: no son "campos tocados".
    after = {**_FILA, "actualizado_por": "otro@x.com", "creado_at": "2026-01-01"}
    assert svc._diff_campos(_FILA, after) == []


def test_diff_campos_sin_cambios():
    assert svc._diff_campos(_FILA, dict(_FILA)) == []
