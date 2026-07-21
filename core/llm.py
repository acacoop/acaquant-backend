"""core/llm.py — transporte LLM único y RUTEO de proveedores (QuantAI, docs/QUANTAI.md).

ESTE es el ÚNICO archivo del sistema que sabe qué proveedores de LLM usamos y
cómo se le habla a cada uno. Cambiar de proveedor — o rutear una tarea a otro —
es tocar SOLO este módulo. El gateway `core/ai.py` (tareas, presupuestos,
trazas) y cualquier caller futuro hablan con `chat()` y no saben con quién.

Separación de responsabilidades:
  core/llm.py  → TRANSPORTE + RUTEO: HTTP, auth, retry, dialecto de cada
                 proveedor (nombres de parámetros, switch de razonamiento),
                 parseo de la respuesta, tool-calling wire format.
  core/ai.py   → GATEWAY: tareas registradas (cada una declara su proveedor),
                 presupuestos diarios, trazas a ia.trazas, contrato
                 nunca-levanta de cara a las features.

## Proveedores (decisión del user 2026-07-21)

- **deepseek** (default) — barato, para el grueso del volumen: copiloto de
  mercado, triage, research. Los datos que ve son PÚBLICOS (mercado).
  Cuidado: sus términos permiten entrenar con lo que se le manda y los datos
  viven en China → JAMÁS datos del negocio.
- **openai** — para la tarea del ASISTENTE DE NEGOCIO. No entrena con datos de
  API, retención de 30 días (0 con ZDR), DPA firmable. Más caro (~3x), pero a
  nuestro volumen la diferencia es de dólares al mes y compra la garantía
  contractual sobre los números de la empresa.

**FAIL-CLOSED (importante):** si el proveedor de una tarea NO está configurado,
la llamada NO se hace y NO cae a otro proveedor — devolvería silenciosamente
datos del negocio a DeepSeek, que es justo lo que este ruteo evita. La feature
degrada (el asistente avisa que no está disponible).

Env vars (las únicas de proveedores, leídas SOLO acá):
  DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL   — proveedor deepseek.
  OPENAI_API_KEY   / OPENAI_BASE_URL     — proveedor openai.
  AI_MODEL_FLASH / AI_MODEL_PRO                 — modelos deepseek por tier.
  AI_MODEL_OPENAI_FLASH / AI_MODEL_OPENAI_PRO   — modelos openai por tier.
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

PROVEEDOR_DEFAULT = "deepseek"

# Dialecto de razonamiento por proveedor. OpenAI usa `reasoning_effort`
# (none|minimal|low|medium|high|xhigh); "none" tiene reportes de ser ignorado
# cuando se manda max_completion_tokens, así que para "apagado" usamos
# "minimal" — predecible y barato. DeepSeek usa `thinking: {type}`.
_REASONING_OPENAI = {"disabled": "minimal", "enabled": "medium"}

_PROVEEDORES: dict[str, dict] = {
    "deepseek": {
        "key_env": "DEEPSEEK_API_KEY",
        "url_env": "DEEPSEEK_BASE_URL",
        "url_default": "https://api.deepseek.com",
        "modelos": {"flash": ("AI_MODEL_FLASH", "deepseek-v4-flash"),
                    "pro":   ("AI_MODEL_PRO",   "deepseek-v4-pro")},
        # el parámetro de techo de salida cambia de nombre según proveedor
        "max_tokens_param": "max_tokens",
        "dialecto": "deepseek",
        # ¿el proveedor se compromete a no entrenar con lo que le mandamos?
        "no_entrena": False,
    },
    "openai": {
        "key_env": "OPENAI_API_KEY",
        "url_env": "OPENAI_BASE_URL",
        "url_default": "https://api.openai.com/v1",
        # gpt-5.6-luna $1/$6 por 1M · terra $2.50/$15 (verificado 2026-07-21)
        "modelos": {"flash": ("AI_MODEL_OPENAI_FLASH", "gpt-5.6-luna"),
                    "pro":   ("AI_MODEL_OPENAI_PRO",   "gpt-5.6-terra")},
        # `max_tokens` está deprecado y NO es compatible con los modelos que
        # razonan — los gpt-5.x exigen max_completion_tokens
        "max_tokens_param": "max_completion_tokens",
        "dialecto": "openai",
        "no_entrena": True,
    },
}


def proveedores() -> tuple[str, ...]:
    return tuple(_PROVEEDORES)


def _cfg_proveedor(proveedor: str | None) -> dict:
    cfg = _PROVEEDORES.get(proveedor or PROVEEDOR_DEFAULT)
    if cfg is None:
        raise ValueError(f"proveedor LLM desconocido: {proveedor!r} "
                         f"(conocidos: {sorted(_PROVEEDORES)})")
    return cfg


def configurado(proveedor: str | None = None) -> bool:
    """True si ESE proveedor tiene credencial. Los callers chequean esto,
    jamás la env var directa (que es detalle del proveedor)."""
    try:
        return bool(os.getenv(_cfg_proveedor(proveedor)["key_env"]))
    except ValueError:
        return False


def no_entrena(proveedor: str | None = None) -> bool:
    """True si el proveedor se compromete contractualmente a no entrenar con
    lo que le mandamos. Lo usa la doc/observabilidad para ser explícita sobre
    a dónde va cada tarea."""
    try:
        return bool(_cfg_proveedor(proveedor)["no_entrena"])
    except ValueError:
        return False


def modelo(tier: str = "flash", proveedor: str | None = None) -> str:
    """ID del modelo para (tier, proveedor), con override por env var."""
    cfg = _cfg_proveedor(proveedor)
    env, default = cfg["modelos"].get(tier) or cfg["modelos"]["flash"]
    return os.getenv(env, default)


def modelo_flash() -> str:
    """Compat: modelo tier flash del proveedor default."""
    return modelo("flash")


def modelo_pro() -> str:
    """Compat: modelo tier pro del proveedor default."""
    return modelo("pro")


def _base_url(cfg: dict) -> str:
    return os.getenv(cfg["url_env"], cfg["url_default"])


@dataclass
class RespuestaLLM:
    """Resultado de una llamada de transporte. `ok=False` + `error` ante
    cualquier fallo — chat() NUNCA levanta excepción."""
    ok: bool
    texto: str | None = None
    razonamiento: str | None = None          # reasoning_content, si el proveedor lo expone
    tool_calls: list[dict] = field(default_factory=list)
    mensaje: dict | None = None              # el message crudo (para re-inyectar en loops de tools)
    tokens_in: int | None = None
    tokens_out: int | None = None
    cache_hit: int | None = None             # tokens servidos desde caché de prefijo
    cache_miss: int | None = None
    latencia_ms: int | None = None
    error: str | None = None


def _armar_body(cfg: dict, *, modelo_id: str, mensajes: list[dict], max_tokens: int,
                thinking: str | None, tools: list[dict] | None) -> dict:
    """Traduce los parámetros neutros al dialecto del proveedor."""
    body: dict = {"model": modelo_id, "messages": mensajes,
                  cfg["max_tokens_param"]: max_tokens}
    if tools:
        body["tools"] = tools
    if cfg["dialecto"] == "openai":
        # store=false SIEMPRE: que la llamada no quede almacenada del lado del
        # proveedor para evals/distillation, sin depender de que el toggle de
        # la organización esté bien puesto (defensa en profundidad).
        body["store"] = False
        if thinking is not None:
            body["reasoning_effort"] = _REASONING_OPENAI.get(thinking, "minimal")
    elif thinking is not None:
        # Shape verificado contra la doc del proveedor (2026-07-11): el default
        # es "enabled" → los callers lo mandan SIEMPRE explícito por tarea.
        body["thinking"] = {"type": thinking}
    return body


def _usage_cache(usage: dict) -> tuple[int | None, int | None]:
    """Tokens de caché normalizados entre dialectos: DeepSeek expone
    prompt_cache_hit/miss_tokens; OpenAI, prompt_tokens_details.cached_tokens."""
    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    if hit is None:
        detalles = usage.get("prompt_tokens_details") or {}
        hit = detalles.get("cached_tokens")
        if hit is not None and miss is None and usage.get("prompt_tokens") is not None:
            miss = max(0, int(usage["prompt_tokens"]) - int(hit))
    return hit, miss


def chat(
    mensajes: list[dict],
    *,
    modelo: str,
    max_tokens: int,
    timeout_s: int,
    thinking: str | None = None,
    tools: list[dict] | None = None,
    reintentos: int = 0,
    proveedor: str | None = None,
) -> RespuestaLLM:
    """Una llamada de chat al proveedor indicado (default: PROVEEDOR_DEFAULT).

    - `mensajes`: lista OpenAI-style ({role, content, ...}) — se manda tal cual.
    - `thinking`: "enabled"/"disabled" NEUTRO — cada proveedor lo traduce a su
      dialecto (thinking / reasoning_effort). None = no mandar nada.
    - `tools`: schemas de function-calling. Si el modelo pide herramientas,
      vuelven en `tool_calls` y `mensaje` trae el message crudo para
      re-inyectarlo en el loop del caller.
    - `reintentos`: cuántas veces reintentar ante timeout / conexión / 5xx.
      Ante 4xx JAMÁS se reintenta (pedido mal armado).

    NUNCA levanta excepción: cualquier fallo → RespuestaLLM(ok=False, error=...).
    """
    try:
        cfg = _cfg_proveedor(proveedor)
    except ValueError as e:
        return RespuestaLLM(ok=False, error=str(e))
    key = os.getenv(cfg["key_env"])
    if not key:
        return RespuestaLLM(
            ok=False,
            error=f"transporte LLM sin configurar para {proveedor or PROVEEDOR_DEFAULT} "
                  f"(falta la API key)")

    import requests

    url = _base_url(cfg) + "/chat/completions"
    body = _armar_body(cfg, modelo_id=modelo, mensajes=mensajes, max_tokens=max_tokens,
                       thinking=thinking, tools=tools)

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
        cache_hit, cache_miss = _usage_cache(usage)
        return RespuestaLLM(
            ok=True,
            texto=(msg.get("content") or "").strip() or None,
            razonamiento=(msg.get("reasoning_content") or "").strip() or None,
            tool_calls=list(msg.get("tool_calls") or []),
            mensaje=msg,
            tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"),
            cache_hit=cache_hit,
            cache_miss=cache_miss,
            latencia_ms=latencia_ms,
        )

    latencia_ms = int((time.perf_counter() - t0) * 1000)
    return RespuestaLLM(ok=False, latencia_ms=latencia_ms, error=ultimo_error)


# ── Saldo REAL de la cuenta del proveedor ────────────────────────────────────
# GET /user/balance — endpoint propio de DeepSeek (verificado contra su doc
# 2026-07-11: is_available + balance_infos[{currency, total_balance,
# granted_balance, topped_up_balance}]). Es el dato de la CUENTA, no una
# inferencia. Los demás proveedores no exponen un equivalente → None.

_SALDO_TTL_S = 300
_saldo_cache: dict = {"ts": 0.0, "valor": None}


def saldo_cuenta(proveedor: str = "deepseek") -> dict | None:
    """Saldo real de la cuenta del proveedor, cacheado 5 min. None si no hay
    key, si el proveedor no expone saldo, o ante fallo sin valor previo.
    Nunca levanta."""
    if proveedor != "deepseek":
        return None
    cfg = _PROVEEDORES["deepseek"]
    key = os.getenv(cfg["key_env"])
    if not key:
        return None
    ahora = time.monotonic()
    if ahora - _saldo_cache["ts"] < _SALDO_TTL_S and _saldo_cache["valor"] is not None:
        return _saldo_cache["valor"]
    try:
        import requests
        resp = requests.get(_base_url(cfg) + "/user/balance",
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
