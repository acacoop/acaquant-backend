"""core/ai.py — gateway único de IA (QuantAI Fase 0, ver docs/QUANTAI.md).

TODA llamada a un LLM del sistema pasa por acá. El gateway resuelve lo que
ninguna feature debería resolver por su cuenta:

- **Tareas registradas** (_TAREAS): cada llamada declara una tarea y de ahí
  salen modelo (tier flash/pro), max_tokens y timeout. El PROMPT vive en el
  módulo de la feature (ej. core/ai_resumen.py) — acá solo el transporte.
- **Proveedor**: DeepSeek (API OpenAI-compatible). Cambiar de proveedor =
  tocar SOLO este módulo (URL base + mapeo de modelos).
- **Presupuesto diario de tokens** (global y por usuario) contra ia.trazas:
  superado → la llamada se niega y la feature degrada. Kill switch de costos.
- **Reintentos**: 1 retry ante timeout / error de conexión / 5xx. Nunca ante 4xx.
- **Traza**: cada llamada (ok o no) deja una fila en SQL `ia.trazas` (tarea,
  modelo, usuario, tokens in/out, latencia, éxito/fallo) — el "job_runs" de la
  IA. Best-effort: si la DB no responde, la llamada sigue igual.

CONTRATO (mismo que core/notify.py): completar() NUNCA propaga excepción.
Devuelve el texto o None; el caller SIEMPRE tiene su camino determinista
(regla de oro 4 del roadmap: todo degrada con gracia).

Env vars:
  DEEPSEEK_API_KEY   — sin ella el gateway está apagado (completar → None).
  DEEPSEEK_BASE_URL  — override de la URL base (default https://api.deepseek.com).
  AI_MODEL_FLASH     — override del modelo tier flash (default deepseek-v4-flash).
  AI_MODEL_PRO       — override del modelo tier pro   (default deepseek-v4-pro).
  AI_BUDGET_TOKENS_DIA          — tope global de tokens/día (default 2.000.000).
  AI_BUDGET_TOKENS_DIA_USUARIO  — tope por usuario/día (default 200.000).
"""
from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

logger = logging.getLogger(__name__)

_MAX_ERROR_CHARS = 300  # techo del texto de error que se persiste en la traza

# Registro de tareas: tier "flash" = redacción/clasificación barata; "pro" =
# razonamiento pesado. El thinking mode se cablea cuando llegue la primera
# tarea pro (P2 triage), verificando el shape del parámetro contra la doc del
# proveedor en ese momento (REGLA #2: no codear contra un API no verificado).
# `model_env` (opcional) = env var que overridea el modelo SOLO para esa tarea.
_TAREAS: dict[str, dict] = {
    "controles_resumen": {"tier": "flash", "max_tokens": 800, "timeout_s": 60,
                          "model_env": "AI_RESUMEN_MODEL"},
    "smoke": {"tier": "flash", "max_tokens": 64, "timeout_s": 30},
    # P2 triage de incidentes (jobs/triage.py): diagnóstico de una falla de job.
    # tier pro (razonamiento). Thinking mode todavía NO cableado (pendiente de
    # verificar el shape del parámetro de DeepSeek — REGLA #2); pro alcanza para v1.
    "triage_incidente": {"tier": "pro", "max_tokens": 700, "timeout_s": 90},
}

_DEFAULT_TAREA = {"tier": "flash", "max_tokens": 800, "timeout_s": 60}


def _config(tarea: str) -> dict:
    cfg = _TAREAS.get(tarea)
    if cfg is None:
        logger.warning("core.ai: tarea desconocida %r — uso config default (flash)", tarea)
        cfg = _DEFAULT_TAREA
    return cfg


def _modelo(cfg: dict) -> str:
    override_env = cfg.get("model_env")
    if override_env and os.getenv(override_env):
        return os.environ[override_env]
    if cfg.get("tier") == "pro":
        return os.getenv("AI_MODEL_PRO", "deepseek-v4-pro")
    return os.getenv("AI_MODEL_FLASH", "deepseek-v4-flash")


def presupuesto_dia_global() -> int:
    """Tope global de tokens/día del gateway. Fuente única — lo usa el check
    interno y la vista de observabilidad (api/services/ia_obs.py)."""
    return int(os.getenv("AI_BUDGET_TOKENS_DIA", "2000000"))


def presupuesto_dia_usuario() -> int:
    return int(os.getenv("AI_BUDGET_TOKENS_DIA_USUARIO", "200000"))


def _presupuesto_excedido(usuario: str | None) -> bool:
    """True si el gasto de HOY (UTC) superó el tope global o el del usuario.
    Best-effort: si la DB no responde NO bloquea — el presupuesto es control de
    costos, no un gate de seguridad."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT coalesce(sum(coalesce(tokens_in, 0) + coalesce(tokens_out, 0)), 0),
                       coalesce(sum(coalesce(tokens_in, 0) + coalesce(tokens_out, 0))
                                FILTER (WHERE usuario = %s), 0)
                FROM ia.trazas
                WHERE ts >= date_trunc('day', now())
                """,
                (usuario,),
            )
            total, del_usuario = cur.fetchone()
        if total >= presupuesto_dia_global():
            logger.warning("core.ai: presupuesto GLOBAL diario agotado (%s tokens hoy)", total)
            return True
        if usuario and del_usuario >= presupuesto_dia_usuario():
            logger.warning(
                "core.ai: presupuesto diario de %s agotado (%s tokens hoy)", usuario, del_usuario
            )
            return True
        return False
    except Exception as e:
        logger.warning("core.ai: no pude chequear el presupuesto (%s) — sigo sin bloquear", e)
        return False


def _trazar(
    tarea: str,
    modelo: str,
    usuario: str | None,
    tokens_in: int | None,
    tokens_out: int | None,
    latencia_ms: int | None,
    ok: bool,
    error: str | None,
) -> None:
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ia.trazas (tarea, modelo, usuario, tokens_in, tokens_out,"
                " latencia_ms, ok, error) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (tarea, modelo, usuario, tokens_in, tokens_out, latencia_ms, ok,
                 error[:_MAX_ERROR_CHARS] if error else None),
            )
    except Exception as e:
        logger.warning("core.ai: no pude registrar la traza de %s (%s)", tarea, e)


def completar(
    tarea: str,
    *,
    system: str,
    user: str,
    usuario: str | None = None,
) -> str | None:
    """Una completion vía el gateway. Devuelve el texto o None (sin key,
    presupuesto agotado, o fallo del proveedor) — NUNCA levanta excepción."""
    try:
        return _completar(tarea, system=system, user=user, usuario=usuario)
    except Exception as e:  # cinturón: el contrato es no propagar JAMÁS
        logger.warning("core.ai: fallo inesperado en %s: %s: %s", tarea, type(e).__name__, e)
        return None


def _completar(tarea: str, *, system: str, user: str, usuario: str | None) -> str | None:
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return None  # gateway apagado — sin traza (sería ruido en cada corrida)
    cfg = _config(tarea)
    modelo = _modelo(cfg)
    if _presupuesto_excedido(usuario):
        _trazar(tarea, modelo, usuario, None, None, None, False, "presupuesto diario agotado")
        return None

    import requests
    url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com") + "/chat/completions"
    body = {
        "model": modelo,
        "max_tokens": cfg["max_tokens"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    t0 = time.perf_counter()
    ultimo_error: str | None = None
    for _intento in (1, 2):
        try:
            resp = requests.post(
                url, headers={"Authorization": f"Bearer {key}"}, json=body,
                timeout=cfg["timeout_s"],
            )
        except requests.RequestException as e:  # timeout / conexión → retry
            ultimo_error = f"{type(e).__name__}: {e}"
            continue
        if resp.status_code >= 500:  # error del proveedor → retry
            ultimo_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            continue
        latencia_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code != 200:  # 4xx: pedido mal armado / key inválida — sin retry
            err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            logger.warning("core.ai %s → %s", tarea, err)
            _trazar(tarea, modelo, usuario, None, None, latencia_ms, False, err)
            return None
        try:
            data = resp.json()
            texto = (data["choices"][0]["message"]["content"] or "").strip()
            usage = data.get("usage") or {}
        except Exception as e:
            _trazar(tarea, modelo, usuario, None, None, latencia_ms, False,
                    f"respuesta inparseable: {e}")
            return None
        _trazar(tarea, modelo, usuario, usage.get("prompt_tokens"),
                usage.get("completion_tokens"), latencia_ms, bool(texto),
                None if texto else "respuesta vacía")
        return texto or None

    latencia_ms = int((time.perf_counter() - t0) * 1000)
    logger.warning("core.ai %s falló tras reintentos: %s", tarea, ultimo_error)
    _trazar(tarea, modelo, usuario, None, None, latencia_ms, False, ultimo_error)
    return None
