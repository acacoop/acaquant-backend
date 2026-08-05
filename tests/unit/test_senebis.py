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
