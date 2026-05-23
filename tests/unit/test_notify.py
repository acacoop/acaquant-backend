"""Tests del notificador de alertas (core/notify.py).

Verifica que sea no-op sin config, que nunca lance, y que la alerta de job
lleve metadata segura (nombre/status) con el último error TRUNCADO.
"""
from __future__ import annotations

import core.notify as notify


def test_send_telegram_noop_sin_config(monkeypatch):
    monkeypatch.setattr(notify, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(notify, "TELEGRAM_CHAT_ID", "")
    assert notify.send_telegram("hola") is False


def test_notify_job_failure_formato_seguro(monkeypatch):
    capturado: dict[str, str] = {}
    monkeypatch.setattr(notify, "send_telegram", lambda text: capturado.update(text=text) or True)

    notify.notify_job_failure(
        "aum", "error", elapsed_s=12.3, n_errors=2,
        last_error="x" * 500,  # error largo → debe truncarse
    )
    txt = capturado["text"]
    assert "aum" in txt
    assert "error" in txt
    assert "Manager.JobRuns" in txt
    # El error de 500 chars no debe entrar entero (se trunca a 180).
    assert "x" * 500 not in txt
    assert "x" * 180 in txt


def test_send_telegram_no_propaga_si_requests_falla(monkeypatch):
    # Garantía clave: un fallo de red al notificar NUNCA tumba al caller.
    monkeypatch.setattr(notify, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(notify, "TELEGRAM_CHAT_ID", "123")
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("red caída")))
    assert notify.send_telegram("hola") is False  # devuelve False, no lanza
