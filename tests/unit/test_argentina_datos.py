"""Tests del cliente argentinadatos + job de persistencia."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.argentina_datos import (
    ArgDataError,
    get_inflacion_mensual,
    get_rems_mes,
    get_riesgo_pais_serie,
    get_riesgo_pais_ultimo,
)


def _mk_response(status, json_data):
    r = MagicMock()
    r.status_code = status
    r.text = "body"
    r.json = MagicMock(return_value=json_data)
    return r


def _mock_mongo():
    coll = MagicMock()
    db = MagicMock()
    db.__getitem__.side_effect = lambda _k: coll
    client = MagicMock()
    client.__getitem__.side_effect = lambda _k: db
    return client, coll


# ─── Cliente ─────────────────────────────────────────────────────────────────


def test_get_riesgo_pais_ultimo_ok():
    with patch("core.argentina_datos.requests.get",
               return_value=_mk_response(200, {"fecha": "2026-04-22", "valor": 1250})):
        out = get_riesgo_pais_ultimo()
    assert out["valor"] == 1250


def test_get_riesgo_pais_serie_ok():
    with patch("core.argentina_datos.requests.get",
               return_value=_mk_response(200, [
                   {"fecha": "2026-04-20", "valor": 1270},
                   {"fecha": "2026-04-21", "valor": 1260},
                   {"fecha": "2026-04-22", "valor": 1250},
               ])):
        out = get_riesgo_pais_serie()
    assert len(out) == 3


def test_get_inflacion_mensual_ok():
    with patch("core.argentina_datos.requests.get",
               return_value=_mk_response(200, [
                   {"fecha": "2026-03-01", "valor": 3.2},
                   {"fecha": "2026-04-01", "valor": 2.8},
               ])):
        out = get_inflacion_mensual()
    assert out[1]["valor"] == 2.8


def test_shape_inesperada_lanza():
    with patch("core.argentina_datos.requests.get",
               return_value=_mk_response(200, "no-soy-una-lista")):
        with pytest.raises(ArgDataError):
            get_riesgo_pais_serie()


def test_status_error_lanza():
    with patch("core.argentina_datos.requests.get",
               return_value=_mk_response(503, "unavailable")):
        with pytest.raises(ArgDataError):
            get_riesgo_pais_ultimo()


def test_get_rems_mes_path():
    """Confirma que rems/{anio}/{mes} arma el path con zero padding."""
    captured = {}

    def fake_get(url, timeout):
        captured["url"] = url
        return _mk_response(200, [{"indicador": "Inflación", "mediana": 2.5}])

    with patch("core.argentina_datos.requests.get", side_effect=fake_get):
        get_rems_mes(2026, 3)
    assert "/v1/rems/2026/03" in captured["url"]


# ─── Job ─────────────────────────────────────────────────────────────────────


def test_run_persiste_3_series():
    from jobs.argentina_datos import run

    rp_data = [{"fecha": "2026-04-22", "valor": 1250}]
    ipc_data = [{"fecha": "2026-03-01", "valor": 3.2}]
    ipcy_data = [{"fecha": "2026-03-01", "valor": 90.5}]

    client, _coll = _mock_mongo()

    # El cliente hace 3 calls distintas — las respondemos por side_effect.
    responses = iter([
        _mk_response(200, rp_data),
        _mk_response(200, ipc_data),
        _mk_response(200, ipcy_data),
    ])

    with (
        patch("jobs.argentina_datos.get_mongo_client", return_value=client),
        patch("core.argentina_datos.requests.get",
              side_effect=lambda *a, **k: next(responses)),
    ):
        res = run()

    assert res["ok"] is True
    # 3 series, cada una con 1 punto
    assert res["series"]["riesgo_pais"]["persistidos"] == 1
    assert res["series"]["inflacion_mensual"]["persistidos"] == 1
    assert res["series"]["inflacion_interanual"]["persistidos"] == 1


def test_run_sanity_descarta_riesgo_pais_anomalo():
    """Valor fuera de rango se descarta; las series válidas se persisten."""
    from jobs.argentina_datos import run

    rp_data = [
        {"fecha": "2026-04-22", "valor": 1250},    # OK
        {"fecha": "2026-04-21", "valor": 999_999}, # rechazado (techo 15000)
    ]
    ipc_data = [{"fecha": "2026-03-01", "valor": 3.2}]
    ipcy_data = [{"fecha": "2026-03-01", "valor": 90.5}]

    client, _coll = _mock_mongo()
    responses = iter([
        _mk_response(200, rp_data),
        _mk_response(200, ipc_data),
        _mk_response(200, ipcy_data),
    ])

    with (
        patch("jobs.argentina_datos.get_mongo_client", return_value=client),
        patch("core.argentina_datos.requests.get",
              side_effect=lambda *a, **k: next(responses)),
    ):
        res = run()

    assert res["ok"] is True
    assert res["series"]["riesgo_pais"]["persistidos"] == 1
    assert res["series"]["riesgo_pais"]["descartados"] == 1


def test_run_only_riesgo_salta_las_otras_series():
    from jobs.argentina_datos import run

    rp_data = [{"fecha": "2026-04-22", "valor": 1250}]
    client, _coll = _mock_mongo()
    with (
        patch("jobs.argentina_datos.get_mongo_client", return_value=client),
        patch("core.argentina_datos.requests.get",
              return_value=_mk_response(200, rp_data)),
    ):
        res = run(solo="riesgo")

    assert "riesgo_pais" in res["series"]
    assert "inflacion_mensual" not in res["series"]


def test_run_una_serie_falla_no_rompe_las_otras():
    """Si /riesgo-pais tira 500, igual persiste las otras dos y reporta ok=False."""
    from jobs.argentina_datos import run

    client, _coll = _mock_mongo()

    responses = iter([
        _mk_response(500, "boom"),                                  # riesgo-pais
        _mk_response(200, [{"fecha": "2026-03-01", "valor": 3.2}]), # ipc OK
        _mk_response(200, [{"fecha": "2026-03-01", "valor": 90}]),  # ipcy OK
    ])

    with (
        patch("jobs.argentina_datos.get_mongo_client", return_value=client),
        patch("core.argentina_datos.requests.get",
              side_effect=lambda *a, **k: next(responses)),
    ):
        res = run()

    assert res["ok"] is False
    assert "error" in res["series"]["riesgo_pais"]
    assert res["series"]["inflacion_mensual"]["persistidos"] == 1
    assert res["series"]["inflacion_interanual"]["persistidos"] == 1
