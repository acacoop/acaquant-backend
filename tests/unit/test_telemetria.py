"""Tests de la telemetría de uso (api/telemetria.py + api/services/uso_modulos.py).

Sin SQL ni red: congela los filtros del hot path (qué se cuenta y qué no), la
acumulación en memoria y la matriz del panel. Doc: docs/OBSERVABILIDAD_ROBUSTEZ.md.
"""
from __future__ import annotations

from api import telemetria
from api.services.uso_modulos import _matriz


def _reset():
    telemetria._reset_para_tests()


def test_registrar_cuenta_modulo_del_path():
    _reset()
    # /api/manager/* mapea al módulo manager vía ENDPOINT_MODULE_PREFIXES (reusado)
    assert telemetria.registrar_request("/api/manager/status", "ana@acavalores.com.ar")
    assert telemetria.registrar_request("/api/manager/status", "ana@acavalores.com.ar")
    snap = telemetria._snapshot_para_tests()
    assert len(snap) == 1
    (email, modulo, _hora), hits = next(iter(snap.items()))
    assert email == "ana@acavalores.com.ar" and modulo == "manager" and hits == 2


def test_registrar_ignora_lo_que_debe():
    _reset()
    assert not telemetria.registrar_request("/api/manager/status", None)          # sin email
    assert not telemetria.registrar_request("/api/manager/status", "")            # vacío
    assert not telemetria.registrar_request("/api/manager/status", "service:web") # service token
    assert not telemetria.registrar_request("/api/health", "ana@x.com")           # health
    assert not telemetria.registrar_request("/api/me", "ana@x.com")               # identidad
    assert not telemetria.registrar_request("/mcp/tools", "ana@x.com")            # MCP
    assert not telemetria.registrar_request("/oauth/token", "ana@x.com")          # OAuth
    # path público sin módulo mapeado (get_module_for_path → None) tampoco cuenta
    assert not telemetria.registrar_request("/api/analitica/curvas", "ana@x.com")
    assert telemetria._snapshot_para_tests() == {}


def test_registrar_normaliza_email():
    _reset()
    telemetria.registrar_request("/api/manager/status", "  ANA@Acavalores.COM.ar ")
    (email, _m, _h), _hits = next(iter(telemetria._snapshot_para_tests().items()))
    assert email == "ana@acavalores.com.ar"


def test_registrar_jamas_levanta(monkeypatch):
    _reset()
    # si el resolver de módulos explota, el hot path NO se rompe
    import api.auth as auth
    monkeypatch.setattr(auth, "get_module_for_path", lambda p: 1 / 0)
    assert telemetria.registrar_request("/api/manager/status", "ana@x.com") is False


def test_matriz_ordena_por_totales():
    rows = [
        ("ana@x.com", "manager", 10),
        ("ana@x.com", "trading", 3),
        ("beto@x.com", "trading", 50),
    ]
    m = _matriz(rows)
    assert m["modulos"] == ["trading", "manager"]        # 53 vs 10
    assert m["usuarios"] == ["beto@x.com", "ana@x.com"]  # 50 vs 13
    assert m["celdas"]["ana@x.com"]["manager"] == 10
    assert m["totales_modulo"] == {"manager": 10, "trading": 53}
    assert m["total"] == 63


def test_matriz_vacia():
    m = _matriz([])
    assert m == {"usuarios": [], "modulos": [], "celdas": {},
                 "totales_modulo": {}, "totales_usuario": {}, "total": 0}
