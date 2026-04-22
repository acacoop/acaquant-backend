"""Tests del cliente dolarapi + job de persistencia.

El cliente es un thin wrapper sobre `requests.get` — mockeamos la libreria
para no depender de red. El job usa el cliente y un Mongo mockeado.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.dolar_api import (
    CASAS_SOPORTADAS,
    DolarApiError,
    get_dolar,
    get_soportadas,
    get_todos,
)


def _mk_response(status: int, json_data):
    r = MagicMock()
    r.status_code = status
    r.text = "body"
    r.json = MagicMock(return_value=json_data)
    return r


# ─── Cliente ─────────────────────────────────────────────────────────────────


def test_get_dolar_ok():
    with patch("core.dolar_api.requests.get",
               return_value=_mk_response(200, {"casa": "oficial", "venta": 1000})):
        out = get_dolar("oficial")
    assert out["casa"] == "oficial"
    assert out["venta"] == 1000


def test_get_dolar_status_error():
    with patch("core.dolar_api.requests.get",
               return_value=_mk_response(500, {})):
        with pytest.raises(DolarApiError):
            get_dolar("oficial")


def test_get_dolar_shape_error():
    with patch("core.dolar_api.requests.get",
               return_value=_mk_response(200, ["not", "a", "dict"])):
        with pytest.raises(DolarApiError):
            get_dolar("oficial")


def test_get_todos_ok():
    payload = [
        {"casa": "oficial",   "venta": 1000},
        {"casa": "mayorista", "venta": 995},
        {"casa": "blue",      "venta": 1250},
        {"casa": "tarjeta",   "venta": 1700},  # ignorada por get_soportadas
    ]
    with patch("core.dolar_api.requests.get",
               return_value=_mk_response(200, payload)):
        out = get_todos()
    assert len(out) == 4


def test_get_soportadas_filtra_casas():
    payload = [
        {"casa": "oficial",   "venta": 1000},
        {"casa": "mayorista", "venta": 995},
        {"casa": "blue",      "venta": 1250},
        {"casa": "tarjeta",   "venta": 1700},
        {"casa": "cripto",    "venta": 1300},
    ]
    with patch("core.dolar_api.requests.get",
               return_value=_mk_response(200, payload)):
        out = get_soportadas()
    casas = {d["casa"] for d in out}
    assert casas == set(CASAS_SOPORTADAS)
    # tarjeta y cripto quedan afuera
    assert "tarjeta" not in casas
    assert "cripto" not in casas


def test_get_shape_lista_vacia_ok():
    with patch("core.dolar_api.requests.get",
               return_value=_mk_response(200, [])):
        out = get_todos()
    assert out == []


# ─── Job ─────────────────────────────────────────────────────────────────────


def _mock_mongo_coll():
    """Devuelve (client_mock, coll_mock) listos para bulk_write."""
    coll = MagicMock()
    db = MagicMock()
    db.__getitem__.side_effect = lambda k: coll
    client = MagicMock()
    client.__getitem__.side_effect = lambda k: db
    return client, coll


def test_run_job_persiste_3_casas():
    from jobs.dolar_api import run

    payload = [
        {"casa": "oficial",   "compra": 990,  "venta": 1000, "fechaActualizacion": "2026-04-23T14:30:00Z"},
        {"casa": "mayorista", "compra": 990,  "venta": 995,  "fechaActualizacion": "2026-04-23T14:30:00Z"},
        {"casa": "blue",      "compra": 1240, "venta": 1250, "fechaActualizacion": "2026-04-23T14:30:00Z"},
    ]
    client, coll = _mock_mongo_coll()

    with (
        patch("jobs.dolar_api.get_mongo_client", return_value=client),
        patch("core.dolar_api.requests.get",
              return_value=_mk_response(200, payload)),
    ):
        res = run()

    assert res["ok"] is True
    assert res["escritos"] == 3
    coll.bulk_write.assert_called_once()
    # 3 UpdateOne, ordered=False
    ops = coll.bulk_write.call_args.args[0]
    assert len(ops) == 3


def test_run_job_api_falla_devuelve_not_ok():
    from jobs.dolar_api import run

    client, _coll = _mock_mongo_coll()
    with (
        patch("jobs.dolar_api.get_mongo_client", return_value=client),
        patch("core.dolar_api.requests.get",
              return_value=_mk_response(503, {})),
    ):
        res = run()

    assert res["ok"] is False
    assert res["escritos"] == 0
    assert "error" in res


def test_run_job_ignora_casas_sin_venta():
    from jobs.dolar_api import run

    payload = [
        {"casa": "oficial",   "compra": 990,  "venta": 1000},
        {"casa": "mayorista", "compra": None, "venta": None},  # será ignorada
        {"casa": "blue",      "compra": 1240, "venta": 1250},
    ]
    client, _coll = _mock_mongo_coll()
    with (
        patch("jobs.dolar_api.get_mongo_client", return_value=client),
        patch("core.dolar_api.requests.get",
              return_value=_mk_response(200, payload)),
    ):
        res = run()

    assert res["ok"] is True
    assert res["escritos"] == 2
