"""core/llm.py — transporte LLM único, provider-agnostic (QuantAI, docs/QUANTAI.md).

ESTE es el ÚNICO archivo del sistema que sabe qué proveedor de LLM usamos
(hoy: DeepSeek, API OpenAI-compatible). Cambiar de proveedor — o rutear una
llamada a otro — es tocar SOLO este módulo. Nadie más en el código nombra al
proveedor: el gateway `core/ai.py` (tareas, presupuestos, trazas) y cualquier
caller futuro hablan con `chat()`.

Separación de responsabilidades:
  core/llm.py  → TRANSPORTE: HTTP, auth, retry, parseo de la respuesta,
                 tool-calling wire format. CERO lógica de negocio.
  core/ai.py   → GATEWAY: tareas registradas, presupuestos diarios, trazas
                 a ia.trazas, contrato nunca-levanta de cara a las features.

Env vars (las únicas del proveedor, leídas SOLO acá):
  DEEPSEEK_API_KEY   — sin ella el transporte está apagado (configurado() → False).
  DEEPSEEK_BASE_URL  — override de la URL base (default https://api.deepseek.com).
  AI_MODEL_FLASH     — override del modelo tier flash (default deepseek-v4-flash).
  AI_MODEL_PRO       — override del modelo tier pro   (default deepseek-v4-pro).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

logger = logging.getLogger(__name__)

_MAX_ERROR_BODY = 200  # chars del body de error que viajan en RespuestaLLM.error


def configurado() -> bool:
    """True si hay credencial del proveedor — los callers chequean ESTO,
    jamás la env var directa (que es detalle del proveedor)."""
    return bool(os.getenv("DEEPSEEK_API_KEY"))


def modelo_flash() -> str:
    """ID del modelo tier flash (redacción/clasificación barata)."""
    return os.getenv("AI_MODEL_FLASH", "deepseek-v4-flash")


def modelo_pro() -> str:
    """ID del modelo tier pro (razonamiento pesado)."""
    return os.getenv("AI_MODEL_PRO", "deepseek-v4-pro")


def _base_url() -> str:
    return os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")


@dataclass
class RespuestaLLM:
    """Resultado de una llamada de transporte. `ok=False` + `error` ante
    cualquier fallo — chat() NUNCA levanta excepción."""
    ok: bool
    texto: str | None = None
    razonamiento: str | None = None          # reasoning_content (thinking), si vino
    tool_calls: list[dict] = field(default_factory=list)
    mensaje: dict | None = None              # el message crudo (para re-inyectar en loops de tools)
    tokens_in: int | None = None
    tokens_out: int | None = None
    cache_hit: int | None = None             # prompt_cache_hit_tokens del proveedor
    cache_miss: int | None = None
    latencia_ms: int | None = None
    error: str | None = None


def chat(
    mensajes: list[dict],
    *,
    modelo: str,
    max_tokens: int,
    timeout_s: int,
    thinking: str | None = None,
    tools: list[dict] | None = None,
    reintentos: int = 0,
) -> RespuestaLLM:
    """Una llamada de chat al proveedor. Puro transporte:

    - `mensajes`: lista OpenAI-style ({role, content, ...}) — se manda tal cual.
    - `thinking`: "enabled"/"disabled" — el shape del switch es detalle del
      proveedor y vive acá. None = no mandar el campo.
    - `tools`: schemas de function-calling (formato OpenAI). Si el modelo pide
      herramientas, vuelven en `tool_calls` y `mensaje` trae el message crudo
      para re-inyectarlo en el loop del caller.
    - `reintentos`: cuántas veces se reintenta ante timeout / error de conexión
      / 5xx del proveedor. Ante 4xx JAMÁS se reintenta (pedido mal armado).

    NUNCA levanta excepción: cualquier fallo → RespuestaLLM(ok=False, error=...).
    """
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return RespuestaLLM(ok=False, error="transporte LLM sin configurar (falta la API key)")

    import requests

    url = _base_url() + "/chat/completions"
    body: dict = {"model": modelo, "max_tokens": max_tokens, "messages": mensajes}
    if thinking is not None:
        # Shape verificado contra la doc del proveedor (2026-07-11): default es
        # "enabled" → los callers lo mandan SIEMPRE explícito por tarea.
        body["thinking"] = {"type": thinking}
    if tools:
        body["tools"] = tools

    t0 = time.perf_counter()
    ultimo_error: str | None = None
    for _intento in range(reintentos + 1):
        try:
            resp = requests.post(
                url, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=timeout_s,
            )
        except requests.RequestException as e:  # timeout / conexión → reintentable
            ultimo_error = f"{type(e).__name__}: {e}"
            continue
        if resp.status_code >= 500:  # error del proveedor → reintentable
            ultimo_error = f"HTTP {resp.status_code}: {resp.text[:_MAX_ERROR_BODY]}"
            continue
        latencia_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code != 200:  # 4xx: pedido mal armado / key inválida — sin retry
            return RespuestaLLM(
                ok=False, latencia_ms=latencia_ms,
                error=f"HTTP {resp.status_code}: {resp.text[:_MAX_ERROR_BODY]}",
            )
        try:
            data = resp.json()
            msg = data["choices"][0]["message"]
            usage = data.get("usage") or {}
        except Exception as e:
            return RespuestaLLM(ok=False, latencia_ms=latencia_ms,
                                error=f"respuesta inparseable: {e}")
        return RespuestaLLM(
            ok=True,
            texto=(msg.get("content") or "").strip() or None,
            razonamiento=(msg.get("reasoning_content") or "").strip() or None,
            tool_calls=list(msg.get("tool_calls") or []),
            mensaje=msg,
            tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"),
            cache_hit=usage.get("prompt_cache_hit_tokens"),
            cache_miss=usage.get("prompt_cache_miss_tokens"),
            latencia_ms=latencia_ms,
        )

    latencia_ms = int((time.perf_counter() - t0) * 1000)
    return RespuestaLLM(ok=False, latencia_ms=latencia_ms, error=ultimo_error)


# ── Saldo REAL de la cuenta del proveedor ────────────────────────────────────
# GET /user/balance (verificado contra la doc del proveedor 2026-07-11:
# is_available + balance_infos[{currency, total_balance, granted_balance,
# topped_up_balance}]). Es el dato de la CUENTA, no una inferencia. El
# proveedor NO expone "tokens restantes" — convertir plata→tokens exigiría
# asumir tabla de precios y mix de modelos, así que no se hace.

_SALDO_TTL_S = 300
_saldo_cache: dict = {"ts": 0.0, "valor": None}


def saldo_cuenta() -> dict | None:
    """Saldo real de la cuenta del proveedor, cacheado 5 min. None si no hay
    key ni valor previo. Nunca levanta."""
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return None
    ahora = time.monotonic()
    if ahora - _saldo_cache["ts"] < _SALDO_TTL_S and _saldo_cache["valor"] is not None:
        return _saldo_cache["valor"]
    try:
        import requests
        resp = requests.get(_base_url() + "/user/balance",
                            headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if resp.status_code != 200:
            logger.warning("core.llm: /user/balance HTTP %s: %s",
                           resp.status_code, resp.text[:120])
            return _saldo_cache["valor"]
        data = resp.json()
        valor = {
            "disponible": bool(data.get("is_available")),
            "saldos": [
                {
                    "moneda": b.get("currency"),
                    "total": b.get("total_balance"),
                    "otorgado": b.get("granted_balance"),
                    "cargado": b.get("topped_up_balance"),
                }
                for b in (data.get("balance_infos") or [])
            ],
        }
        _saldo_cache.update(ts=ahora, valor=valor)
        return valor
    except Exception as e:
        logger.warning("core.llm: no pude leer el saldo del proveedor (%s)", e)
        return _saldo_cache["valor"]
