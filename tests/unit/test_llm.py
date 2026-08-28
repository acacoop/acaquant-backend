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


def _payload(content="hola", reasoning=None, usage=None):
    msg = {"content": content}
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


def test_thinking_en_el_body(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    capturado = {}

    def post(url, headers=None, json=None, timeout=None):
        capturado.update(json)
        return _resp(payload=_payload())

    import requests
    monkeypatch.setattr(requests, "post", post)
    llm.chat(MENSAJES, modelo="m", max_tokens=10, timeout_s=5, thinking="disabled")
    assert capturado["thinking"] == {"type": "disabled"}
    # sin thinking → el campo NO viaja (el default lo decide el proveedor)
    capturado.clear()
    llm.chat(MENSAJES, modelo="m", max_tokens=10, timeout_s=5)
    assert "thinking" not in capturado


def test_invariante_solo_llm_cablea_proveedores():
    """ARQUITECTURA (congelado 2026-07-21): en core/ y api/ el CABLEADO de los
    proveedores (env vars de credencial, URLs base, IDs de modelo) vive SOLO en
    core/llm.py. Mencionar el formato de wire ("schema OpenAI") está bien; leer
    OPENAI_API_KEY fuera del transporte, NO. Si este test falla, alguien salteó
    el ruteo — y el ruteo es una decisión de PRIVACIDAD (qué datos van a qué
    proveedor), no un detalle de implementación."""
    patron = re.compile(
        r"DEEPSEEK_API_KEY|OPENAI_API_KEY|api\.deepseek\.com|api\.openai\.com"
        r"|deepseek-v4|gpt-5\.", re.IGNORECASE)
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
        f"cableado de proveedor fuera de core/llm.py: {violaciones}"
    )


# ── Ruteo multi-proveedor (decisión user 2026-07-21) ─────────────────────────

def test_openai_usa_su_dialecto(monkeypatch):
    """El body cambia por proveedor: max_completion_tokens (max_tokens está
    deprecado y es incompatible con los modelos que razonan), reasoning_effort
    en vez de thinking, y store=false SIEMPRE (que no quede almacenado del
    lado del proveedor sin depender del toggle de la organización)."""
    monkeypatch.setenv("OPENAI_API_KEY", "k-openai")
    capturado = {}

    def post(url, headers=None, json=None, timeout=None):
        capturado.update(url=url, body=json)
        return _resp(payload=_payload())

    import requests
    monkeypatch.setattr(requests, "post", post)
    llm.chat(MENSAJES, modelo="gpt-x", max_tokens=1234, timeout_s=5,
             thinking="disabled", proveedor="openai")
    body = capturado["body"]
    assert body["max_completion_tokens"] == 1234 and "max_tokens" not in body
    assert body["store"] is False
    assert "thinking" not in body
    # VERIFICADO contra el proveedor (400 real 2026-07-21): gpt-5.6 acepta
    # none/low/medium/high/xhigh y NO 'minimal' — este assert congela el fix
    assert body["reasoning_effort"] in ("none", "low", "medium", "high", "xhigh")
    assert body["reasoning_effort"] != "minimal"
    assert "openai" in capturado["url"]


def test_reasoning_effort_configurable_por_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("AI_OPENAI_REASONING_OFF", "low")
    capturado = {}

    def post(url, headers=None, json=None, timeout=None):
        capturado.update(json)
        return _resp(payload=_payload())

    import requests
    monkeypatch.setattr(requests, "post", post)
    llm.chat(MENSAJES, modelo="gpt-x", max_tokens=10, timeout_s=5,
             thinking="disabled", proveedor="openai")
    assert capturado["reasoning_effort"] == "low"  # sin deploy, por env


def test_deepseek_conserva_su_dialecto(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    capturado = {}

    def post(url, headers=None, json=None, timeout=None):
        capturado.update(body=json)
        return _resp(payload=_payload())

    import requests
    monkeypatch.setattr(requests, "post", post)
    llm.chat(MENSAJES, modelo="ds", max_tokens=10, timeout_s=5, thinking="disabled")
    assert capturado["body"]["max_tokens"] == 10
    assert capturado["body"]["thinking"] == {"type": "disabled"}
    assert "store" not in capturado["body"]


def test_cache_de_openai_se_normaliza(monkeypatch):
    """OpenAI expone prompt_tokens_details.cached_tokens; DeepSeek,
    prompt_cache_hit_tokens. La traza guarda lo mismo para los dos."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    payload = _payload(usage={"prompt_tokens": 100, "completion_tokens": 20,
                              "prompt_tokens_details": {"cached_tokens": 80}})
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _resp(payload=payload))
    r = llm.chat(MENSAJES, modelo="gpt-x", max_tokens=10, timeout_s=5, proveedor="openai")
    assert r.cache_hit == 80 and r.cache_miss == 20


def test_proveedor_sin_key_no_llama_ni_cae_a_otro(monkeypatch):
    """FAIL-CLOSED del ruteo: sin la key del proveedor de la tarea NO se
    llama a nadie. Caer al proveedor default mandaría datos del negocio
    justo adonde el ruteo los quiere evitar."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k-deepseek")   # el otro SÍ está
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    llamadas = []
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **kw: llamadas.append(1))
    r = llm.chat(MENSAJES, modelo="gpt-x", max_tokens=10, timeout_s=5, proveedor="openai")
    assert r.ok is False and not llamadas
    assert llm.configurado("openai") is False and llm.configurado("deepseek") is True


def test_proveedor_desconocido_no_levanta():
    r = llm.chat(MENSAJES, modelo="x", max_tokens=10, timeout_s=5, proveedor="inventado")
    assert r.ok is False and "desconocido" in r.error




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


@pytest.mark.parametrize("tier,esperado", [("flash", llm.modelo_flash), ("pro", llm.modelo_pro)])
def test_modelos_default_viven_en_llm(monkeypatch, tier, esperado):
    monkeypatch.delenv("AI_MODEL_FLASH", raising=False)
    monkeypatch.delenv("AI_MODEL_PRO", raising=False)
    assert isinstance(esperado(), str) and esperado()
