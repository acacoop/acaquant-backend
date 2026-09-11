"""core/llm.py — LA ÚNICA PUERTA HACIA UN MODELO DE IA.

Todo el sistema que quiera hablarle a un modelo pasa por acá. Nadie más sabe
la URL, la clave ni cómo se le habla a cada proveedor.

Para qué sirve que esté todo en un solo archivo: el día que cambiemos de
proveedor, o que agreguemos otro, se toca ESTE archivo y nada más. Las
features que usan IA no se enteran.

Hay dos proveedores y NO son intercambiables:

  deepseek  barato. Pero sus términos le permiten entrenar con lo que le
            mandamos → SOLO datos públicos de mercado.
  openai    más caro. No entrena con lo que entra por la API → es el único
            al que se le pueden mandar datos de la empresa.

⚠️ Si el proveedor que le toca a una tarea no tiene su clave puesta, la
llamada NO SE HACE y no se manda a otro. Mandarla al otro sería justamente
filtrarle datos de la empresa al que puede entrenar con ellos. Prefiere no
funcionar antes que funcionar mal y en silencio.

Quién lo llama: `core/ai.py` (que además lleva el presupuesto y la traza) y
`agente/explicar.py` para preguntar si hay clave puesta.

Variables de entorno (se leen SOLO acá):
  DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL
  OPENAI_API_KEY   / OPENAI_BASE_URL
  AI_MODEL_FLASH / AI_MODEL_PRO                 — modelos de deepseek
  AI_MODEL_OPENAI_FLASH / AI_MODEL_OPENAI_PRO   — modelos de openai
  AI_OPENAI_REASONING_OFF / _ON                 — cuánto "piensa" openai
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from dotenv import load_dotenv

# El .env se lee acá y no desde la terminal: un `source .env` de bash rompe
# los valores que tienen `&` adentro y el error que ves después habla de otra
# cosa.
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_RAIZ, ".env"))

logger = logging.getLogger(__name__)

# Cuánto texto del error del proveedor se guarda. Generoso porque un error de
# pedido mal armado recién dice QUÉ parámetro rechaza en el medio del mensaje.
_MAX_ERROR_BODY = 500

PROVEEDOR_DEFAULT = "deepseek"

# La ficha de cada proveedor. Todo lo que lo distingue está acá adentro.
_PROVEEDORES: dict[str, dict] = {
    "deepseek": {
        "key_env": "DEEPSEEK_API_KEY",
        "url_env": "DEEPSEEK_BASE_URL",
        "url_default": "https://api.deepseek.com",
        # qué modelo usar según se pida el rápido y barato o el caro y capaz
        "modelos": {"flash": ("AI_MODEL_FLASH", "deepseek-v4-flash"),
                    "pro":   ("AI_MODEL_PRO",   "deepseek-v4-pro")},
        # cada proveedor llama distinto al tope de texto que puede devolver
        "max_tokens_param": "max_tokens",
        "dialecto": "deepseek",
        # ¿se compromete por contrato a NO entrenar con lo que le mandamos?
        "no_entrena": False,
    },
    "openai": {
        "key_env": "OPENAI_API_KEY",
        "url_env": "OPENAI_BASE_URL",
        "url_default": "https://api.openai.com/v1",
        "modelos": {"flash": ("AI_MODEL_OPENAI_FLASH", "gpt-5.6-luna"),
                    "pro":   ("AI_MODEL_OPENAI_PRO",   "gpt-5.6-terra")},
        # los modelos que razonan rechazan `max_tokens`: piden este otro
        "max_tokens_param": "max_completion_tokens",
        "dialecto": "openai",
        "no_entrena": True,
    },
}


def _cfg_proveedor(proveedor: str | None) -> dict:
    """La ficha de un proveedor. Levanta si el nombre no existe."""
    cfg = _PROVEEDORES.get(proveedor or PROVEEDOR_DEFAULT)
    if cfg is None:
        raise ValueError(f"proveedor LLM desconocido: {proveedor!r} "
                         f"(conocidos: {sorted(_PROVEEDORES)})")
    return cfg


def configurado(proveedor: str | None = None) -> bool:
    """¿Tiene puesta su clave? Se pregunta ACÁ y nunca leyendo la variable de
    entorno por fuera: cuál es se cambia en este archivo."""
    try:
        return bool(os.getenv(_cfg_proveedor(proveedor)["key_env"]))
    except ValueError:
        return False


def no_entrena(proveedor: str | None = None) -> bool:
    """¿Este proveedor se comprometió a no entrenar con lo nuestro? Es lo que
    mira `core/ai.py` para decidir si una tarea con datos de la empresa puede
    salir o no."""
    try:
        return bool(_cfg_proveedor(proveedor)["no_entrena"])
    except ValueError:
        return False


def modelo(tier: str = "flash", proveedor: str | None = None) -> str:
    """El nombre del modelo a usar. `tier` es "flash" (barato) o "pro" (caro).
    Si no existe ese tier, cae en flash."""
    cfg = _cfg_proveedor(proveedor)
    env, default = cfg["modelos"].get(tier) or cfg["modelos"]["flash"]
    return os.getenv(env, default)


def _base_url(cfg: dict) -> str:
    return os.getenv(cfg["url_env"], cfg["url_default"])


def _reasoning_openai(thinking: str) -> str:
    """Cuánto tiene que "pensar" openai antes de contestar. Pensar más cuesta
    más tokens, así que el default es no pensar."""
    if thinking == "enabled":
        return os.getenv("AI_OPENAI_REASONING_ON", "medium")
    return os.getenv("AI_OPENAI_REASONING_OFF", "none")


@dataclass
class RespuestaLLM:
    """Lo que devuelve una llamada. Si algo falló, `ok` es False y el motivo
    está en `error` — nunca se levanta una excepción."""
    ok: bool
    texto: str | None = None
    razonamiento: str | None = None   # lo que "pensó", si el proveedor lo muestra
    tokens_in: int | None = None      # cuánto texto entró (lo que se paga)
    tokens_out: int | None = None     # cuánto salió
    cache_hit: int | None = None      # de lo que entró, cuánto salió del caché
    cache_miss: int | None = None     # (el caché es ~10 veces más barato)
    latencia_ms: int | None = None
    error: str | None = None


def _armar_body(cfg: dict, *, modelo_id: str, mensajes: list[dict],
                max_tokens: int, thinking: str | None) -> dict:
    """Arma el pedido en el idioma del proveedor. Los dos hablan parecido pero
    no igual, y esta función es la que traduce."""
    body: dict = {"model": modelo_id, "messages": mensajes,
                  cfg["max_tokens_param"]: max_tokens}
    if cfg["dialecto"] == "openai":
        # Que openai NO guarde la conversación de su lado. Va siempre, sin
        # depender de que el interruptor de la cuenta esté bien puesto.
        body["store"] = False
        if thinking is not None:
            body["reasoning_effort"] = _reasoning_openai(thinking)
    elif thinking is not None:
        body["thinking"] = {"type": thinking}
    return body


def _usage_cache(usage: dict) -> tuple[int | None, int | None]:
    """Cuántos tokens vinieron del caché del proveedor. Cada uno lo informa
    con otro nombre, así que se normaliza a un solo par de números."""
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
    reintentos: int = 0,
    proveedor: str | None = None,
) -> RespuestaLLM:
    """Una llamada al modelo. Es lo único que hace este archivo.

    - `mensajes`: la conversación, como lista de {role, content}.
    - `thinking`: "enabled" / "disabled" / None. Cada proveedor lo traduce.
    - `reintentos`: sólo sirve para fallas pasajeras (se cortó la red, el
      proveedor devolvió un error suyo). Si el pedido está mal armado o la
      clave es inválida NO se reintenta: volver a mandar lo mismo da lo mismo.

    NUNCA levanta una excepción. Si algo falla, devuelve ok=False y el motivo.
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
    body = _armar_body(cfg, modelo_id=modelo, mensajes=mensajes,
                       max_tokens=max_tokens, thinking=thinking)

    t0 = time.perf_counter()
    ultimo_error: str | None = None
    for _intento in range(reintentos + 1):
        try:
            resp = requests.post(url, headers={"Authorization": f"Bearer {key}"},
                                 json=body, timeout=timeout_s)
        except requests.RequestException as e:
            # se cortó la red o venció el tiempo → vale volver a intentar
            ultimo_error = f"{type(e).__name__}: {e}"
            continue
        if resp.status_code >= 500:
            # el problema es del proveedor → vale volver a intentar
            ultimo_error = f"HTTP {resp.status_code}: {resp.text[:_MAX_ERROR_BODY]}"
            continue
        latencia_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code != 200:
            # 4xx: el pedido está mal armado o la clave no sirve. Reintentar
            # daría exactamente el mismo error, así que se corta acá.
            return RespuestaLLM(
                ok=False, latencia_ms=latencia_ms,
                error=f"HTTP {resp.status_code}: {resp.text[:_MAX_ERROR_BODY]}")
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
            tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"),
            cache_hit=cache_hit,
            cache_miss=cache_miss,
            latencia_ms=latencia_ms,
        )

    latencia_ms = int((time.perf_counter() - t0) * 1000)
    return RespuestaLLM(ok=False, latencia_ms=latencia_ms, error=ultimo_error)
