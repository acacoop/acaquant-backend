"""Tests para api/services/simulaciones.py — CRUD aislado de Mongo via mock."""
from datetime import UTC, datetime
from unittest.mock import MagicMock

from bson import ObjectId

from api.services import simulaciones as svc
from api.services.simulaciones import (
    Posicion,
    SimulacionCreate,
    SimulacionUpdate,
)


def _fake_coll(monkeypatch):
    """Reemplaza _coll() por un MagicMock; lo devuelve para que el test asserts."""
    coll = MagicMock()
    monkeypatch.setattr(svc, "_coll", lambda: coll)
    return coll


def _doc(**overrides):
    """Doc base de Simulacion estilo Mongo, sobreescribible."""
    base = {
        "_id": ObjectId(),
        "user_email": "ana@x.com",
        "nombre": "Mi cartera",
        "posiciones": [{"ticker": "TZX26", "importe": 500000.0}],
        "creado": datetime(2026, 4, 26, tzinfo=UTC),
        "actualizado": datetime(2026, 4, 26, tzinfo=UTC),
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# crear_simulacion
# ─────────────────────────────────────────────────────────────────────────────


def test_crear_simulacion_inserta_y_devuelve_doc_con_id(monkeypatch):
    coll = _fake_coll(monkeypatch)
    fake_id = ObjectId()
    coll.insert_one.return_value.inserted_id = fake_id

    sim = svc.crear_simulacion(
        "ana@x.com",
        SimulacionCreate(nombre="t1", posiciones=[Posicion(ticker="TZX26", importe=100)]),
    )

    coll.insert_one.assert_called_once()
    inserted = coll.insert_one.call_args[0][0]
    assert inserted["user_email"] == "ana@x.com"
    assert inserted["nombre"] == "t1"
    assert inserted["posiciones"] == [{"ticker": "TZX26", "importe": 100.0}]
    assert sim.id == str(fake_id)
    assert sim.user_email == "ana@x.com"
    assert sim.creado == sim.actualizado


def test_crear_acepta_posiciones_vacias(monkeypatch):
    coll = _fake_coll(monkeypatch)
    coll.insert_one.return_value.inserted_id = ObjectId()
    sim = svc.crear_simulacion("a@b.com", SimulacionCreate(nombre="vacía"))
    assert sim.posiciones == []


# ─────────────────────────────────────────────────────────────────────────────
# listar_simulaciones
# ─────────────────────────────────────────────────────────────────────────────


def test_listar_filtra_por_user_email_y_ordena_desc(monkeypatch):
    coll = _fake_coll(monkeypatch)
    docs = [_doc(nombre="vieja"), _doc(nombre="nueva")]
    coll.find.return_value.sort.return_value = iter(docs)

    sims = svc.listar_simulaciones("ana@x.com")

    coll.find.assert_called_once_with({"user_email": "ana@x.com"})
    coll.find.return_value.sort.assert_called_once_with("actualizado", -1)
    assert [s.nombre for s in sims] == ["vieja", "nueva"]


# ─────────────────────────────────────────────────────────────────────────────
# obtener_simulacion
# ─────────────────────────────────────────────────────────────────────────────


def test_obtener_devuelve_doc_si_existe_y_es_del_usuario(monkeypatch):
    coll = _fake_coll(monkeypatch)
    doc = _doc()
    coll.find_one.return_value = doc

    sim = svc.obtener_simulacion(str(doc["_id"]), "ana@x.com")

    coll.find_one.assert_called_once_with({"_id": doc["_id"], "user_email": "ana@x.com"})
    assert sim is not None and sim.id == str(doc["_id"])


def test_obtener_devuelve_none_si_id_invalido(monkeypatch):
    coll = _fake_coll(monkeypatch)
    sim = svc.obtener_simulacion("no-es-objectid", "ana@x.com")
    assert sim is None
    coll.find_one.assert_not_called()


def test_obtener_devuelve_none_si_no_existe_o_es_de_otro(monkeypatch):
    coll = _fake_coll(monkeypatch)
    coll.find_one.return_value = None
    sim = svc.obtener_simulacion(str(ObjectId()), "ana@x.com")
    assert sim is None


# ─────────────────────────────────────────────────────────────────────────────
# actualizar_simulacion
# ─────────────────────────────────────────────────────────────────────────────


def test_actualizar_solo_nombre(monkeypatch):
    coll = _fake_coll(monkeypatch)
    doc = _doc(nombre="renombrada")
    coll.find_one_and_update.return_value = doc

    sim = svc.actualizar_simulacion(
        str(doc["_id"]), "ana@x.com", SimulacionUpdate(nombre="renombrada"),
    )

    update_arg = coll.find_one_and_update.call_args[0][1]["$set"]
    assert "nombre" in update_arg and update_arg["nombre"] == "renombrada"
    assert "posiciones" not in update_arg
    assert "actualizado" in update_arg
    assert sim is not None and sim.nombre == "renombrada"


def test_actualizar_solo_posiciones(monkeypatch):
    coll = _fake_coll(monkeypatch)
    doc = _doc()
    coll.find_one_and_update.return_value = doc

    svc.actualizar_simulacion(
        str(doc["_id"]),
        "ana@x.com",
        SimulacionUpdate(posiciones=[Posicion(ticker="GD30", importe=200)]),
    )

    update_arg = coll.find_one_and_update.call_args[0][1]["$set"]
    assert update_arg["posiciones"] == [{"ticker": "GD30", "importe": 200.0}]
    assert "nombre" not in update_arg


def test_actualizar_sin_cambios_no_escribe_pero_devuelve_doc_actual(monkeypatch):
    coll = _fake_coll(monkeypatch)
    doc = _doc()
    coll.find_one.return_value = doc

    sim = svc.actualizar_simulacion(str(doc["_id"]), "ana@x.com", SimulacionUpdate())

    coll.find_one_and_update.assert_not_called()
    coll.find_one.assert_called_once()
    assert sim is not None


def test_actualizar_id_invalido_devuelve_none(monkeypatch):
    coll = _fake_coll(monkeypatch)
    sim = svc.actualizar_simulacion("xx", "ana@x.com", SimulacionUpdate(nombre="x"))
    assert sim is None
    coll.find_one_and_update.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# eliminar_simulacion
# ─────────────────────────────────────────────────────────────────────────────


def test_eliminar_true_si_borra(monkeypatch):
    coll = _fake_coll(monkeypatch)
    coll.delete_one.return_value.deleted_count = 1
    assert svc.eliminar_simulacion(str(ObjectId()), "ana@x.com") is True


def test_eliminar_false_si_no_existe(monkeypatch):
    coll = _fake_coll(monkeypatch)
    coll.delete_one.return_value.deleted_count = 0
    assert svc.eliminar_simulacion(str(ObjectId()), "ana@x.com") is False


def test_eliminar_id_invalido_devuelve_false_sin_tocar_db(monkeypatch):
    coll = _fake_coll(monkeypatch)
    assert svc.eliminar_simulacion("no-es-objectid", "ana@x.com") is False
    coll.delete_one.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Cálculo
# ─────────────────────────────────────────────────────────────────────────────


def test_cantidad_nominal_renta_fija_cotiza_por_100():
    """Bono que cotiza a 95 y se compra por 950k → 1000 VN nominales."""
    assert svc._cantidad_nominal(950_000, 95.0, "Títulos Públicos") == 1_000_000


def test_cantidad_nominal_fci_cotiza_unitario():
    """FCI con valor cuota 100 y compra 500k → 5000 cuotas."""
    assert svc._cantidad_nominal(500_000, 100.0, "FCI") == 5000.0


def test_cantidad_nominal_sin_precio_devuelve_none():
    assert svc._cantidad_nominal(100_000, None, "Títulos Públicos") is None
    assert svc._cantidad_nominal(100_000, 0, "Títulos Públicos") is None


def test_proyectar_cashflows_tasa_fija_suma_amort_e_interes():
    """tasa_fija: monto = (amortizacion + interes) por 100 VN, escalado."""
    flujos = [
        {"fecha": "2026-12-31", "amortizacion": 50.0, "interes": 5.0},
        {"fecha": "2027-06-30", "amortizacion": 50.0, "interes": 2.5},
    ]
    cf = svc._proyectar_cashflows("tasa_fija", flujos, 1_000_000, datetime(2026, 4, 1, tzinfo=UTC))
    # 1M VN → ratio = 10000. (50+5)*10000 = 550_000.
    assert cf == [
        {"fecha": "2026-12-31", "monto": 550_000.0},
        {"fecha": "2027-06-30", "monto": 525_000.0},
    ]


def test_proyectar_cashflows_cer_usa_pcts_resueltos():
    """CER: amortizacion_pct + cupon_sobre_residual ya resueltos en pct."""
    flujos = [{"fecha": "2026-12-31", "amortizacion_pct": 30.0, "cupon_sobre_residual": 1.5}]
    cf = svc._proyectar_cashflows("cer", flujos, 200_000, datetime(2026, 1, 1, tzinfo=UTC))
    # ratio = 2000. (30 + 1.5) * 2000 = 63_000.
    assert cf == [{"fecha": "2026-12-31", "monto": 63_000.0}]


def test_proyectar_cashflows_descarta_pasados():
    flujos = [
        {"fecha": "2025-01-01", "amortizacion": 100.0, "interes": 0},  # pasado
        {"fecha": "2027-01-01", "amortizacion": 100.0, "interes": 0},
    ]
    cf = svc._proyectar_cashflows("tasa_fija", flujos, 100, datetime(2026, 6, 1, tzinfo=UTC))
    assert len(cf) == 1 and cf[0]["fecha"] == "2027-01-01"


def test_proyectar_cashflows_curva_no_soportada_devuelve_vacio():
    """Sin shape de flujos definido (ej. tamar) devuelve vacío sin romper."""
    flujos = [{"fecha": "2026-12-31", "algo": 1}]
    assert svc._proyectar_cashflows("tamar", flujos, 100, datetime(2026, 1, 1, tzinfo=UTC)) == []


def test_moneda_de_prioriza_cartera():
    assert svc._moneda_de("cer", "CARTERA USD") == "USD"
    assert svc._moneda_de("soberanos", "CARTERA ARS") == "ARS"


def test_moneda_de_cae_a_curva_sin_cartera():
    assert svc._moneda_de("cer", None) == "ARS"
    assert svc._moneda_de("soberanos", None) == "USD"
    assert svc._moneda_de("dolar_linked", None) == "ARS-linked-USD"
    assert svc._moneda_de(None, None) == "OTRA"


def test_ponderar_promedia_por_importe():
    items = [
        {"importe": 100, "tea": 0.30},   # peso 100
        {"importe": 300, "tea": 0.40},   # peso 300
    ]
    # (0.30*100 + 0.40*300) / 400 = 0.375
    assert svc._ponderar(items, "tea") == 0.375


def test_ponderar_ignora_items_sin_valor_o_sin_peso():
    items = [
        {"importe": 100, "tea": None},
        {"importe": 0, "tea": 0.5},
        {"importe": 200, "tea": 0.20},
    ]
    assert svc._ponderar(items, "tea") == 0.20


def test_ponderar_devuelve_none_si_no_hay_data():
    assert svc._ponderar([{"importe": 100, "tea": None}], "tea") is None


def test_composicion_agrupa_y_ordena_desc(monkeypatch):
    items = [
        {"importe": 100, "curva": "cer"},
        {"importe": 300, "curva": "tasa_fija"},
        {"importe": 200, "curva": "cer"},
    ]
    out = svc._composicion_por(items, "curva", monto_total=600)
    assert out == [
        {"valor": "cer",       "importe": 300, "pct": 50.0},
        {"valor": "tasa_fija", "importe": 300, "pct": 50.0},
    ]


def test_composicion_sin_clasificar_para_items_sin_valor():
    items = [
        {"importe": 100, "emisor": "TESORO"},
        {"importe": 50, "emisor": None},
    ]
    out = svc._composicion_por(items, "emisor", 150)
    valores = {x["valor"] for x in out}
    assert "(sin clasificar)" in valores


def test_calcular_vacio_devuelve_estructura_consistente():
    out = svc.calcular([])
    assert out["posiciones_enriquecidas"] == []
    assert out["cashflows"] == []
    assert out["metricas"]["monto_total"] == 0
    assert out["alertas"] == []


def test_calcular_marca_alerta_si_ticker_desconocido(monkeypatch):
    monkeypatch.setattr(svc, "_enriquecer_lote", lambda tk: {})
    out = svc.calcular([Posicion(ticker="FAKE", importe=100)])
    assert any("FAKE" in a for a in out["alertas"])
    assert out["posiciones_enriquecidas"][0]["precio"] is None


def test_calcular_pipeline_end_to_end_un_bono(monkeypatch):
    """Path completo con un solo bono CER mockeado, verificando shape de salida."""
    fake_meta = {
        "TZX26": {
            "ticker_corto":     "TZX26",
            "ticker_largo":     "MERV - XMEV - TZX26 - 24hs",
            "tipo":             "cer",
            "curva":            "cer",
            "fecha_emision":    "2024-06-30",
            "fecha_vencimiento": "2099-06-30",  # futuro lejano para no filtrar flujos
            "valor_nominal":    100,
            "flujos": [{
                "fecha": "2099-06-30",
                "amortizacion_pct": 100.0,
                "cupon_sobre_residual": 5.0,
            }],
            "cer_emision":      100.0,
            "precio":           90.0,
            "tea":              0.30,
            "tem":              0.022,
            "paridad":          95.0,
            "duration":         1.5,
            "ts_precio":        datetime(2026, 4, 26, tzinfo=UTC),
            "clase_activo":     "Títulos Públicos",
            "calificacion":     "CCC+",
            "emisor":           "TESORO",
            "cartera":          "CARTERA ARS",
            "unidad":           "[9240] TZX26",
        }
    }
    monkeypatch.setattr(svc, "_enriquecer_lote", lambda tk: fake_meta)

    out = svc.calcular([Posicion(ticker="TZX26", importe=900_000)])

    pos = out["posiciones_enriquecidas"][0]
    # 900k / (90/100) = 1M VN
    assert pos["cantidad_nominal"] == 1_000_000
    assert pos["moneda"] == "ARS"
    assert pos["emisor"] == "TESORO"

    # Cashflow único: ratio 10000 × (100 + 5) = 1_050_000
    assert out["cashflows"] == [{"mes": "2099-06", "monto": 1_050_000.0}]

    # Métricas ponderadas con un solo bono = el valor del bono
    assert out["metricas"]["tea_ponderada"] == 0.30
    assert out["metricas"]["duration_ponderada"] == 1.5
    assert out["metricas"]["monto_total"] == 900_000

    # Composición 100% CER / Títulos Públicos / TESORO / ARS
    assert out["composicion"]["por_curva"][0]["valor"] == "cer"
    assert out["composicion"]["por_curva"][0]["pct"] == 100.0
