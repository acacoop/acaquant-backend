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
