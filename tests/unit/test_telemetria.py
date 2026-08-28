"""Tests de la telemetría de LATENCIA (api/telemetria.py).

Sin SQL ni red: congela los filtros del hot path (qué se registra y qué no),
la normalización de paths y la acumulación en memoria.
"""
from __future__ import annotations

from api import telemetria


def _reset():
    telemetria._reset_para_tests()


def test_registrar_acumula_por_endpoint_normalizado():
    _reset()
    assert telemetria.registrar_request("/api/valuaciones/805/mensual", 120.4, 200)
    assert telemetria.registrar_request("/api/valuaciones/912/mensual", 300.9, 200)
    snap = telemetria._snapshot_para_tests()
    assert len(snap) == 1  # ambos ids colapsan al mismo endpoint
    (endpoint, _hora), (n, total, mx, lentas, errores) = next(iter(snap.items()))
    assert endpoint == "/api/valuaciones/{id}/mensual"
    assert n == 2 and total == 420 and mx == 300
    assert lentas == 0 and errores == 0


def test_registrar_cuenta_lentas_y_errores():
    _reset()
    assert telemetria.registrar_request("/api/operaciones/ops/serie", 1500.0, 200)
    assert telemetria.registrar_request("/api/operaciones/ops/serie", 80.0, 502)
    snap = telemetria._snapshot_para_tests()
    (_k, (n, _total, mx, lentas, errores)) = next(iter(snap.items()))
    assert n == 2 and mx == 1500 and lentas == 1 and errores == 1


def test_registrar_ignora_lo_que_debe():
    _reset()
    assert not telemetria.registrar_request("/api/health", 5.0, 200)      # health
    # Estos tres los descarta la MISMA regla: no cuelgan de /api/. (Los dos
    # primeros eran del MCP, borrado el 2026-08-28; el test los conserva porque
    # lo que congela es la regla, no la ruta.)
    assert not telemetria.registrar_request("/mcp", 5.0, 200)
    assert not telemetria.registrar_request("/oauth/token", 5.0, 200)
    assert not telemetria.registrar_request("/favicon.ico", 5.0, 200)
    assert telemetria._snapshot_para_tests() == {}


def test_normalizar_colapsa_uuid_y_largos():
    n = telemetria._normalizar
    assert n("/api/ordenes/550e8400-e29b-41d4-a716-446655440000") == "/api/ordenes/{id}"
    assert n("/api/ordenes/dia") == "/api/ordenes/dia"
    assert n("/api/x/" + "a" * 60) == "/api/x/{id}"


def test_buffer_con_techo():
    _reset()
    viejo = telemetria._MAX_BUFFER
    telemetria._MAX_BUFFER = 3
    try:
        for i in range(5):
            telemetria.registrar_request(f"/api/e{i}", 10.0, 200)
        assert len(telemetria._snapshot_para_tests()) == 3  # descarta el resto
    finally:
        telemetria._MAX_BUFFER = viejo
        _reset()
