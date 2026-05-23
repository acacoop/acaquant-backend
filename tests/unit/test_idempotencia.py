"""Tests de la idempotencia de envío de órdenes (anti doble-orden).

Mockean el motor de envío y el store; verifican la lógica del wrapper
send_order: sin clave = comportamiento idéntico; clave nueva = manda 1 vez;
clave duplicada = NO manda, devuelve el resultado del primero; error = lo
registra y relanza.
"""
from __future__ import annotations

import pytest

import api.services._idempotencia as idem
import api.services.ordenes as ordenes


def test_sin_clave_llama_impl_y_no_toca_idem(monkeypatch):
    llamado = {}

    def _impl(**kw):
        llamado["impl"] = True
        return {"ok": True, "cl_ord_id": "X"}
    monkeypatch.setattr(ordenes, "_send_order_impl", _impl)

    def _no_llamar(_k):
        raise AssertionError("idem.reservar no debía llamarse sin clave")
    monkeypatch.setattr(idem, "reservar", _no_llamar)

    r = ordenes.send_order(ticker="AL30", side="BUY", size=1, price=1.0)
    assert r["ok"] is True
    assert llamado["impl"] is True


def test_clave_nueva_manda_una_vez_y_guarda(monkeypatch):
    monkeypatch.setattr(ordenes, "_send_order_impl", lambda **kw: {"ok": True, "cl_ord_id": "ABC"})
    monkeypatch.setattr(idem, "reservar", lambda _k: True)
    guardado = {}
    monkeypatch.setattr(idem, "guardar_resultado", lambda k, r: guardado.update(key=k, res=r))

    r = ordenes.send_order(ticker="AL30", side="BUY", size=1, price=1.0, client_order_id="k1")
    assert r["cl_ord_id"] == "ABC"
    assert guardado["key"] == "k1"
    assert guardado["res"]["cl_ord_id"] == "ABC"


def test_clave_duplicada_no_manda_devuelve_primero(monkeypatch):
    def _no_mandar(**kw):
        raise AssertionError("un duplicado NO debe mandar al broker")
    monkeypatch.setattr(ordenes, "_send_order_impl", _no_mandar)
    monkeypatch.setattr(idem, "reservar", lambda _k: False)
    monkeypatch.setattr(idem, "esperar_resultado", lambda _k: {"ok": True, "cl_ord_id": "ABC", "duplicate": True})

    r = ordenes.send_order(ticker="AL30", side="BUY", size=1, price=1.0, client_order_id="k1")
    assert r["cl_ord_id"] == "ABC"      # el resultado del PRIMER envío
    assert r["duplicate"] is True


def test_error_en_envio_se_registra_y_relanza(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("broker caído")
    monkeypatch.setattr(ordenes, "_send_order_impl", _boom)
    monkeypatch.setattr(idem, "reservar", lambda _k: True)
    errs = {}
    monkeypatch.setattr(idem, "guardar_error", lambda k, m: errs.update(key=k, msg=m))

    with pytest.raises(RuntimeError):
        ordenes.send_order(ticker="AL30", side="BUY", size=1, price=1.0, client_order_id="k1")
    assert errs["key"] == "k1"
    assert "broker" in errs["msg"]
