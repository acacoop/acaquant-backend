"""Cache de los endpoints pesados de la vista Comercial."""
from __future__ import annotations

import pytest

from api.cache import clear_cache
from api.routers import operaciones


@pytest.fixture(autouse=True)
def _cache_limpio():
    clear_cache()
    yield
    clear_cache()


def _operador_kwargs(**cambios):
    kwargs = {
        "operador": ["a@x.com"],
        "moneda": "ARS",
        "nivel_1": ["CLIENTES"],
        "nivel_2": None,
        "nivel_3": None,
        "nivel_4": None,
        "nivel_5": None,
        "referido": None,
        "division": None,
        "fecha": "2026-09-16",
        "desde": "2026-09-01",
    }
    kwargs.update(cambios)
    return kwargs


def _serie_kwargs(**cambios):
    kwargs = {
        "operador": ["a@x.com"],
        "metric": "aum",
        "moneda": "ARS",
        "id_cuenta": "123",
        "nivel_1": ["CLIENTES"],
        "nivel_2": None,
        "nivel_3": None,
        "nivel_4": None,
        "nivel_5": None,
        "referido": None,
        "division": None,
    }
    kwargs.update(cambios)
    return kwargs


@pytest.mark.parametrize(
    ("handler", "service_name", "kwargs"),
    [
        (operaciones.comercial_operador, "operador_comercial", _operador_kwargs()),
        (operaciones.comercial_serie, "serie_comercial", _serie_kwargs()),
    ],
)
def test_llamadas_identicas_reusan_cache_con_operador_multiselect(
    monkeypatch, handler, service_name, kwargs
):
    llamadas = []

    def servicio_falso(**service_kwargs):
        llamadas.append(service_kwargs)
        return {"llamada": len(llamadas), "operador": service_kwargs["operador"]}

    monkeypatch.setattr(operaciones._com_sql, service_name, servicio_falso)

    primera = handler(**kwargs)
    segunda = handler(**kwargs)

    assert primera == segunda
    assert primera["operador"] == ["a@x.com"]
    assert len(llamadas) == 1


def test_operador_no_comparte_cache_entre_parametros_distintos(monkeypatch):
    llamadas = []

    def servicio_falso(**kwargs):
        llamadas.append(kwargs)
        return {"llamada": len(llamadas)}

    monkeypatch.setattr(operaciones._com_sql, "operador_comercial", servicio_falso)

    base = operaciones.comercial_operador(**_operador_kwargs())
    otra_moneda = operaciones.comercial_operador(**_operador_kwargs(moneda="USD"))
    otro_filtro = operaciones.comercial_operador(**_operador_kwargs(nivel_1=["PRODUCTORES"]))

    assert [base["llamada"], otra_moneda["llamada"], otro_filtro["llamada"]] == [1, 2, 3]
    assert len(llamadas) == 3


def test_serie_no_comparte_cache_entre_parametros_distintos(monkeypatch):
    llamadas = []

    def servicio_falso(**kwargs):
        llamadas.append(kwargs)
        return {"llamada": len(llamadas)}

    monkeypatch.setattr(operaciones._com_sql, "serie_comercial", servicio_falso)

    base = operaciones.comercial_serie(**_serie_kwargs())
    otra_metrica = operaciones.comercial_serie(**_serie_kwargs(metric="volumen"))
    otra_cuenta = operaciones.comercial_serie(**_serie_kwargs(id_cuenta="456"))
    otro_filtro = operaciones.comercial_serie(**_serie_kwargs(nivel_1=["PRODUCTORES"]))

    assert [
        base["llamada"], otra_metrica["llamada"], otra_cuenta["llamada"], otro_filtro["llamada"]
    ] == [1, 2, 3, 4]
    assert len(llamadas) == 4