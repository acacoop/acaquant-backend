"""Tests de core/llm.py — el transporte LLM único (provider-agnostic).

Congelan dos cosas:
1. El contrato de chat(): nunca levanta, parsea texto/tools/usage, retry solo
   ante fallos reintentables (timeout/conexión/5xx), jamás ante 4xx.
2. La INVARIANTE DE ARQUITECTURA: el proveedor se nombra SOLO en core/llm.py —
   ningún otro archivo de core/ ni api/ puede mencionarlo. Es lo que garantiza
   que "cambiar/rutear proveedor = tocar un solo archivo" siga siendo cierto.
"""
from __future__ import annotations

import os
import re
from types import SimpleNamespace

import pytest

from core import llm

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _resp(status=200, payload=None, text=""):
    return SimpleNamespace(status_code=status, text=text, json=lambda: payload)


def _payload(content="hola", tool_calls=None, reasoning=None, usage=None):
    msg = {"content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"message": msg}], "usage": usage or {
        "prompt_tokens": 10, "completion_tokens": 5,
        "prompt_cache_hit_tokens": 3, "prompt_cache_miss_tokens": 7,
    }}


MENSAJES = [{"role": "system", "content": "sos un test"},
            {"role": "user", "content": "hola"}]


def test_sin_key_devuelve_error_claro(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert llm.configurado() is False
    r = llm.chat(MENSAJES, modelo="m", max_tokens=10, timeout_s=5)
    assert r.ok is False
    assert "API key" in r.error


def test_chat_parsea_texto_y_usage(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _resp(payload=_payload()))
    r = llm.chat(MENSAJES, modelo="m", max_tokens=10, timeout_s=5)
    assert r.ok and r.texto == "hola"
    assert (r.tokens_in, r.tokens_out) == (10, 5)
    assert (r.cache_hit, r.cache_miss) == (3, 7)
    assert r.tool_calls == []


def test_chat_devuelve_tool_calls_y_mensaje_crudo(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    calls = [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]
    import requests
    monkeypatch.setattr(requests, "post",
                        lambda *a, **kw: _resp(payload=_payload(content="", tool_calls=calls)))
    r = llm.chat(MENSAJES, modelo="m", max_tokens=10, timeout_s=5, tools=[{"type": "function"}])
    assert r.ok and r.tool_calls == calls
    assert r.mensaje["tool_calls"] == calls  # crudo, para re-inyectar en el loop


def test_retry_ante_5xx_y_exito_al_segundo(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    intentos = []

    def post(*a, **kw):
        intentos.append(1)
        if len(intentos) == 1:
            return _resp(status=503, text="unavailable")
        return _resp(payload=_payload())

    import requests
    monkeypatch.setattr(requests, "post", post)
    r = llm.chat(MENSAJES, modelo="m", max_tokens=10, timeout_s=5, reintentos=1)
    assert r.ok and len(intentos) == 2


def test_4xx_no_reintenta(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    intentos = []

    def post(*a, **kw):
        intentos.append(1)
        return _resp(status=401, text="bad key")

    import requests
    monkeypatch.setattr(requests, "post", post)
    r = llm.chat(MENSAJES, modelo="m", max_tokens=10, timeout_s=5, reintentos=2)
    assert r.ok is False and len(intentos) == 1
    assert "HTTP 401" in r.error


def test_reintentos_agotados_devuelve_ultimo_error(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _resp(status=500, text="boom"))
    r = llm.chat(MENSAJES, modelo="m", max_tokens=10, timeout_s=5, reintentos=1)
    assert r.ok is False and "HTTP 500" in r.error


def test_thinking_y_tools_en_el_body(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    capturado = {}

    def post(url, headers=None, json=None, timeout=None):
        capturado.update(json)
        return _resp(payload=_payload())

    import requests
    monkeypatch.setattr(requests, "post", post)
    llm.chat(MENSAJES, modelo="m", max_tokens=10, timeout_s=5,
             thinking="disabled", tools=[{"type": "function"}])
    assert capturado["thinking"] == {"type": "disabled"}
    assert capturado["tools"] == [{"type": "function"}]
    # sin thinking → el campo NO viaja (el default lo decide el proveedor)
    capturado.clear()
    llm.chat(MENSAJES, modelo="m", max_tokens=10, timeout_s=5)
    assert "thinking" not in capturado


def test_invariante_solo_llm_nombra_al_proveedor():
    """ARQUITECTURA (congelado 2026-07-21): en core/ y api/ el string del
    proveedor aparece SOLO en core/llm.py. Si este test falla, alguien metió
    una referencia directa al proveedor fuera del transporte — moverla a
    core/llm.py (el ruteo multi-proveedor depende de esta invariante)."""
    patron = re.compile(r"deepseek", re.IGNORECASE)
    violaciones = []
    for carpeta in ("core", "api"):
        for dirpath, _dirs, files in os.walk(os.path.join(RAIZ, carpeta)):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(dirpath, f)
                if os.path.relpath(path, RAIZ).replace("\\", "/") == "core/llm.py":
                    continue
                with open(path, encoding="utf-8") as fh:
                    if patron.search(fh.read()):
                        violaciones.append(os.path.relpath(path, RAIZ))
    assert not violaciones, (
        f"referencias al proveedor fuera de core/llm.py: {violaciones}"
    )


def test_gateway_delega_en_llm(monkeypatch):
    """core/ai._completar pasa por core.llm.chat (no arma HTTP propio)."""
    from core import ai
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(ai, "motivo_presupuesto", lambda u: None)
    monkeypatch.setattr(ai, "_trazar", lambda *a, **kw: 42)
    llamado = {}

    def chat_fake(mensajes, **kw):
        llamado["mensajes"] = mensajes
        llamado.update(kw)
        return llm.RespuestaLLM(ok=True, texto="ok", tokens_in=1, tokens_out=1)

    monkeypatch.setattr(llm, "chat", chat_fake)
    texto, traza_id = ai.completar_con_traza("smoke", system="s", user="u")
    assert texto == "ok" and traza_id == 42
    assert llamado["mensajes"][0]["content"] == "s"
    assert llamado["reintentos"] == 1  # el camino simple conserva su retry


def test_tools_loop_historial_se_inyecta(monkeypatch):
    """completar_con_tools(historial=...) inserta los turnos entre system y user."""
    from core import ai
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(ai, "motivo_presupuesto", lambda u: None)
    monkeypatch.setattr(ai, "_trazar", lambda *a, **kw: 1)
    visto = {}

    def chat_fake(mensajes, **kw):
        visto["mensajes"] = list(mensajes)
        return llm.RespuestaLLM(ok=True, texto="fin", mensaje={"content": "fin"})

    monkeypatch.setattr(llm, "chat", chat_fake)
    hist = [{"role": "user", "content": "antes"}, {"role": "assistant", "content": "resp"}]
    texto, _tid, _ctx = ai.completar_con_tools(
        "smoke", system="s", user="ahora", tools=[], ejecutar=lambda n, a: "",
        historial=hist)
    assert texto == "fin"
    roles = [m["role"] for m in visto["mensajes"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert visto["mensajes"][-1]["content"] == "ahora"


@pytest.mark.parametrize("tier,esperado", [("flash", llm.modelo_flash), ("pro", llm.modelo_pro)])
def test_modelos_default_viven_en_llm(monkeypatch, tier, esperado):
    monkeypatch.delenv("AI_MODEL_FLASH", raising=False)
    monkeypatch.delenv("AI_MODEL_PRO", raising=False)
    assert isinstance(esperado(), str) and esperado()
