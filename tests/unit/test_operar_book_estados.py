"""Tests del endpoint /api/operar/order-book — los TRES desenlaces.

Nace del reporte "elegís un ticker y no trae nada" (2026-07-22): un libro
vacío pero FRESCO caía al camino de suscripción y el cliente recibía 202 para
siempre, sin forma de distinguir "esperá 5s" de "esto no va a llegar nunca".
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.routers import operar as mod


def _ahora(delta_s: float = 0) -> str:
    return (datetime.now(UTC) - timedelta(seconds=delta_s)).isoformat()


def _book(*, edad_s: float, bids=None, offers=None) -> dict:
    return {
        "ticker": "MERV - XMEV - AL30 - 24hs",
        "updated_at": _ahora(edad_s),
        "book": {"bids": bids, "offers": offers},
        "metrics": {"last_price": 1234.5},
    }


@pytest.fixture
def sin_efectos(monkeypatch):
    monkeypatch.setattr(mod, "bump_last_used", lambda t: None)
    monkeypatch.setattr(mod, "_existe_en_pyrofex", lambda t: True)
    monkeypatch.setattr(mod, "subscribe",
                        lambda t: {"ok": True, "created": True, "active_count": 3})


def test_con_puntas_devuelve_el_libro(monkeypatch, sin_efectos):
    monkeypatch.setattr(mod, "get_order_book",
                        lambda t, plazo=None: _book(edad_s=2, bids=[{"price": 1, "size": 1}]))
    r = mod.get_book(ticker="AL30", plazo="24hs")
    assert r["book"]["bids"] and "sin_puntas" not in r


def test_libro_vacio_pero_FRESCO_se_devuelve_igual(monkeypatch, sin_efectos):
    """El caso del reporte. Mercado cerrado o papel sin oferta: NO es una falla
    de suscripción, y devolver 202 dejaba la pantalla en blanco para siempre.
    Se devuelve 200 con la marca, para que la vista lo diga y el último precio
    —que sí es real— quede visible."""
    monkeypatch.setattr(mod, "get_order_book", lambda t, plazo=None: _book(edad_s=5))
    r = mod.get_book(ticker="AL30", plazo="24hs")
    assert r["sin_puntas"] is True
    assert r["metrics"]["last_price"] == 1234.5      # el dato útil no se pierde
    assert "mercado cerrado" in r["motivo"]


def test_libro_vacio_y_ABANDONADO_re_suscribe(monkeypatch, sin_efectos):
    """La razón por la que el chequeo de puntas existía: una fila que el motor
    dejó de refrescar es un DOM muerto y hay que volver a suscribir."""
    monkeypatch.setattr(mod, "get_order_book",
                        lambda t, plazo=None: _book(edad_s=mod._FRESCURA_S + 60))
    r = mod.get_book(ticker="AL30", plazo="24hs")
    assert r.status_code == 202


def test_el_202_dice_hace_cuanto_que_la_fila_no_se_toca(monkeypatch, sin_efectos):
    """Sin este dato el cliente reintenta a ciegas: no puede distinguir 'recién
    lo pedí' de 'el motor está caído'."""
    monkeypatch.setattr(mod, "get_order_book",
                        lambda t, plazo=None: _book(edad_s=600))
    r = mod.get_book(ticker="AL30", plazo="24hs")
    assert r.status_code == 202
    import json
    cuerpo = json.loads(r.body)
    assert cuerpo["fila_edad_s"] >= 590


def test_sin_fila_es_202_con_edad_desconocida(monkeypatch, sin_efectos):
    monkeypatch.setattr(mod, "get_order_book", lambda t, plazo=None: None)
    r = mod.get_book(ticker="AL30", plazo="24hs")
    assert r.status_code == 202
    import json
    assert json.loads(r.body)["fila_edad_s"] is None


def test_con_puntas_pero_ABANDONADO_no_se_sirve_como_vigente(monkeypatch, sin_efectos):
    """El agujero que faltaba cubrir, y por eso el bug vivió meses.

    Había test para "vacío + abandonado" y para "con puntas + fresco", pero NINGUNO
    para "con puntas + abandonado". El endpoint decía `_tiene_puntas(book) or fresca`,
    así que una fila con puntas se servía sin mirar de cuándo eran y jamás llegaba al
    camino de re-suscripción. Caso real (2026-08-07): RKLB mostraba bid 9130 / ask
    9170 de una fila de 16 DÍAS mientras el último precio real era 10860.

    Un libro rancio es peor que uno vacío: el vacío avisa, el viejo te deja mandar
    una orden límite contra puntas que ya no existen. La frescura manda.
    """
    monkeypatch.setattr(mod, "get_order_book", lambda t, plazo=None: _book(
        edad_s=16 * 24 * 3600,
        bids=[{"price": 9130, "size": 10}], offers=[{"price": 9170, "size": 10}]))

    import json

    r = mod.get_book(ticker="RKLB", plazo="24hs")

    assert getattr(r, "status_code", None) == 202, "una fila de 16 días NO es un libro"
    cuerpo = json.loads(r.body)
    assert cuerpo["status"] == "subscribing"
    assert cuerpo["fila_edad_s"] > 1_000_000          # se ve que estaba abandonada
    assert cuerpo["last_price"] == 1234.5             # el último precio conocido sí viaja
