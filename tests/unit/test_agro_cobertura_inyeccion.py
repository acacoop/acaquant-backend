"""get_pase_cobertura no relee lo que le pasan.

`/api/derivados/agro` es el endpoint #1 (17% del tiempo de la app) y llama a
esta función DOS veces, una por plaza. Tasas, dólares y sintéticos son
idénticos para Rosario y Bahía, y encima `get_pase_agro` ya los tenía leídos:
sin inyección, lo mismo viajaba a la base hasta 3 veces por request, a ~8.5ms
el viaje (cProfile 2026-08-13: 66 viajes por request).

Estos tests fijan las DOS mitades del contrato:
  · con datos inyectados NO se lee nada (el ahorro),
  · sin ellos se lee igual que siempre (los otros llamadores no se enteran).
"""
from __future__ import annotations

import pytest

from api.services import agro_cobertura as cob
from api.services import camara_cereales as cam

_VACIO = {"cereales": []}


@pytest.fixture
def contador(monkeypatch):
    """Cuenta cuántas veces se pega a la base por cada insumo."""
    n = {"tasas": 0, "dolares": 0, "cam": 0, "cam_bahia": 0, "sinteticos": 0}

    def _fake(clave, valor):
        def _f(*a, **k):
            n[clave] += 1
            return valor
        return _f

    monkeypatch.setattr(cam, "get_tasas_cobertura", _fake("tasas", {}))
    monkeypatch.setattr(cam, "get_dolares_referencia", _fake("dolares", {}))
    monkeypatch.setattr(cam, "get_camara_cereales", _fake("cam", _VACIO))
    monkeypatch.setattr(cam, "get_camara_cereales_bahia", _fake("cam_bahia", _VACIO))
    monkeypatch.setattr(cob, "_sinteticos_por_ym", _fake("sinteticos", {}))
    return n


def test_con_inyeccion_no_lee_nada(contador):
    cob.get_pase_cobertura([], tasas={}, dolares={}, cam=_VACIO, sinteticos={})
    assert contador == {"tasas": 0, "dolares": 0, "cam": 0, "cam_bahia": 0,
                        "sinteticos": 0}, "inyectado y aun así fue a la base"


def test_sin_inyeccion_lee_como_siempre(contador):
    """El comportamiento viejo se mantiene: otros llamadores no cambian."""
    cob.get_pase_cobertura([])
    assert contador["tasas"] == 1
    assert contador["dolares"] == 1
    assert contador["cam"] == 1
    assert contador["sinteticos"] == 1
    assert contador["cam_bahia"] == 0        # plaza rosario → no toca Bahía


def test_bahia_sin_cam_inyectada_usa_la_de_bahia(contador):
    """La cámara es lo ÚNICO que difiere entre plazas: si no se inyecta, la de
    Bahía se lee de su propia fuente."""
    cob.get_pase_cobertura([], plaza="bahia", tasas={}, dolares={}, sinteticos={})
    assert contador["cam_bahia"] == 1
    assert contador["cam"] == 0
    assert contador["tasas"] == 0            # lo inyectado sigue sin leerse
