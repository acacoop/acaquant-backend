"""Tests para api/agent/types.py — serialización robusta de tool results."""
import json
from datetime import UTC, date, datetime

from api.agent.types import tool_result_message


def test_tool_result_con_datetime_se_serializa():
    """Tools que leen de Mongo devuelven datetime — no debe romper json.dumps."""
    result = {
        "ok": True,
        "data": {
            "ticker": "TX26",
            "ts_ultimo_trade": datetime(2026, 4, 21, 17, 30, tzinfo=UTC),
            "fecha_vencimiento": date(2026, 11, 9),
        },
    }
    msg = tool_result_message("toolu_abc", result)

    assert msg["role"] == "user"
    assert msg["content"][0]["type"] == "tool_result"
    assert msg["content"][0]["tool_use_id"] == "toolu_abc"

    # El content es un string JSON parseable con las fechas como ISO.
    parsed = json.loads(msg["content"][0]["content"])
    assert parsed["data"]["ts_ultimo_trade"] == "2026-04-21T17:30:00+00:00"
    assert parsed["data"]["fecha_vencimiento"] == "2026-11-09"


def test_tool_result_con_datetime_anidado_en_lista():
    """Los tools devuelven muchas veces list[dict] con fechas adentro."""
    result = {
        "ok": True,
        "data": [
            {"fecha": date(2026, 1, 1), "valor": 1.0},
            {"fecha": date(2026, 1, 2), "valor": 1.1},
        ],
    }
    msg = tool_result_message("toolu_xyz", result)
    parsed = json.loads(msg["content"][0]["content"])
    assert parsed["data"][0]["fecha"] == "2026-01-01"
    assert parsed["data"][1]["valor"] == 1.1


def test_tool_result_trunca_a_20k():
    """El content se corta a 20k chars para no explotar el contexto."""
    big_payload = {"data": "x" * 30000}
    msg = tool_result_message("toolu_big", big_payload)
    assert len(msg["content"][0]["content"]) == 20000


def test_tool_result_tipos_normales_siguen_andando():
    """Sanity: strings, ints, bools, None."""
    result = {
        "ok": True,
        "data": {"nombre": "TX26", "cantidad": 100, "activo": True, "nota": None},
    }
    msg = tool_result_message("toolu_1", result)
    parsed = json.loads(msg["content"][0]["content"])
    assert parsed["data"]["nombre"] == "TX26"
    assert parsed["data"]["cantidad"] == 100
    assert parsed["data"]["activo"] is True
    assert parsed["data"]["nota"] is None
