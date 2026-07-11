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
    # tier pro (razonamiento). max_tokens ALTO a propósito: v4-pro razona antes de
    # responder y el razonamiento cuenta como output — con 700 se quedaba sin lugar
    # para la respuesta final y volvía vacía (verificado en ia.trazas: tok_out=700,
    # "respuesta vacía"). 2500 le da lugar para pensar Y contestar.
    "triage_incidente": {"tier": "pro", "max_tokens": 2500, "timeout_s": 120},
    # P3 copiloto de mesa (api/services/copiloto.py): Q&A sobre los datos de UNA
    # vista de mercado, provistos en el prompt. tier flash (no hay razonamiento
    # pesado: los datos ya vienen dados). max_tokens generoso a propósito —
    # lección del P2: techo chico + input grande = respuesta vacía. 2000→3000
    # el 2026-07-11: en ia.trazas hubo respuestas de 1796 tok_out (al ras) y
    # una vacía.
    "copiloto_vista": {"tier": "flash", "max_tokens": 3000, "timeout_s": 60},
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


# ── Config editable (ia.config, editable desde Manager → OBSERVABILIDAD → IA) ──
# Precedencia: tabla ia.config > env var > default del código. Cache 60s para
# no pegarle a la DB en cada llamada; best-effort (DB caída → último conocido).

_CONFIG_DB_TTL_S = 60
_config_db_cache: dict = {"ts": 0.0, "valores": {}}


def _config_db() -> dict:
    ahora = time.monotonic()
    if ahora - _config_db_cache["ts"] < _CONFIG_DB_TTL_S:
        return _config_db_cache["valores"]
    valores = _config_db_cache["valores"]  # fallback: último conocido
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT clave, valor FROM ia.config")
            valores = {r[0]: int(r[1]) for r in cur.fetchall()}
    except Exception as e:
        logger.warning("core.ai: no pude leer ia.config (%s) — uso env/default", e)
    _config_db_cache.update(ts=ahora, valores=valores)
    return valores


def invalidate_config_cache() -> None:
    """La llama el service al editar presupuestos para que el gateway los vea ya."""
    _config_db_cache["ts"] = 0.0


# ── Saldo REAL de la cuenta del proveedor ────────────────────────────────────
# GET /user/balance (verificado contra la doc de DeepSeek 2026-07-11:
# is_available + balance_infos[{currency, total_balance, granted_balance,
# topped_up_balance}]). Es el dato de la CUENTA, no una inferencia. El
# proveedor NO expone "tokens restantes" — convertir plata→tokens exigiría
# asumir tabla de precios y mix de modelos, así que no se hace.

_SALDO_TTL_S = 300
_saldo_cache: dict = {"ts": 0.0, "valor": None}


def saldo_proveedor() -> dict | None:
    """Saldo real de la cuenta DeepSeek, cacheado 5 min. None si no hay key
    ni valor previo. Nunca levanta."""
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return None
    ahora = time.monotonic()
    if ahora - _saldo_cache["ts"] < _SALDO_TTL_S and _saldo_cache["valor"] is not None:
        return _saldo_cache["valor"]
    try:
        import requests
        url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com") + "/user/balance"
        resp = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if resp.status_code != 200:
            logger.warning("core.ai: /user/balance HTTP %s: %s",
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
        logger.warning("core.ai: no pude leer el saldo del proveedor (%s)", e)
        return _saldo_cache["valor"]


def presupuesto_dia_global() -> int:
    """Tope global de tokens/día del gateway — TECHO DURO del sistema: aunque
    la suma de topes por usuario lo supere, el gasto total del día no lo pasa
    (cada llamada chequea los dos). Fuente única — lo usa el check interno y
    la vista de observabilidad (api/services/ia_obs.py)."""
    v = _config_db().get("budget_dia_global")
    return v if v else int(os.getenv("AI_BUDGET_TOKENS_DIA", "2000000"))


def presupuesto_dia_usuario() -> int:
    """Default 1M (subido de 200k el 2026-07-11): el copiloto cuesta ~22k
    tokens/pregunta (medido en ia.trazas) y 200k = ~9 preguntas cortaba un día
    normal de shadow. 1M ≈ 45 preguntas ≈ centavos en flash."""
    v = _config_db().get("budget_dia_usuario")
    return v if v else int(os.getenv("AI_BUDGET_TOKENS_DIA_USUARIO", "1000000"))


def motivo_presupuesto(usuario: str | None) -> str | None:
    """'global' o 'usuario' según QUÉ tope superó el gasto de HOY (UTC), o None
    si hay margen. Público a propósito: las features lo consultan ANTES de
    llamar para devolver un error CLARO ("tu límite" vs "el del sistema") en
    vez de un genérico — el gateway igual re-chequea. Best-effort: si la DB no
    responde NO bloquea — el presupuesto es control de costos, no un gate de
    seguridad."""
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
            return "global"
        if usuario and del_usuario >= presupuesto_dia_usuario():
            logger.warning(
                "core.ai: presupuesto diario de %s agotado (%s tokens hoy)", usuario, del_usuario
            )
            return "usuario"
        return None
    except Exception as e:
        logger.warning("core.ai: no pude chequear el presupuesto (%s) — sigo sin bloquear", e)
        return None


def _presupuesto_excedido(usuario: str | None) -> bool:
    return motivo_presupuesto(usuario) is not None


_MAX_DETALLE_CHARS = 600     # extracto del pedido (lo pasa el caller, ej. la pregunta)
_MAX_RESPUESTA_CHARS = 1500  # extracto de la respuesta del modelo


def _trazar(
    tarea: str,
    modelo: str,
    usuario: str | None,
    tokens_in: int | None,
    tokens_out: int | None,
    latencia_ms: int | None,
    ok: bool,
    error: str | None,
    detalle: str | None = None,
    respuesta: str | None = None,
) -> int | None:
    """Persiste la traza y devuelve su id (para asociar feedback 👍/👎),
    o None si la DB no respondió — best-effort, nunca corta la llamada.
    `detalle`/`respuesta` son extractos legibles para el panel de
    OBSERVABILIDAD (qué se preguntó / qué contestó), capados."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ia.trazas (tarea, modelo, usuario, tokens_in, tokens_out,"
                " latencia_ms, ok, error, detalle, respuesta)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " RETURNING id",
                (tarea, modelo, usuario, tokens_in, tokens_out, latencia_ms, ok,
                 error[:_MAX_ERROR_CHARS] if error else None,
                 detalle[:_MAX_DETALLE_CHARS] if detalle else None,
                 respuesta[:_MAX_RESPUESTA_CHARS] if respuesta else None),
            )
            return cur.fetchone()[0]
    except Exception as e:
        logger.warning("core.ai: no pude registrar la traza de %s (%s)", tarea, e)
        return None


def completar(
    tarea: str,
    *,
    system: str,
    user: str,
    usuario: str | None = None,
    detalle: str | None = None,
) -> str | None:
    """Una completion vía el gateway. Devuelve el texto o None (sin key,
    presupuesto agotado, o fallo del proveedor) — NUNCA levanta excepción.
    `detalle`: extracto legible del pedido (ej. la pregunta del usuario) que
    queda en la traza para el panel de OBSERVABILIDAD."""
    texto, _traza_id = completar_con_traza(
        tarea, system=system, user=user, usuario=usuario, detalle=detalle
    )
    return texto


def completar_con_traza(
    tarea: str,
    *,
    system: str,
    user: str,
    usuario: str | None = None,
    detalle: str | None = None,
) -> tuple[str | None, int | None]:
    """Igual que completar() pero devuelve también el id de la traza en
    ia.trazas (o None si no se pudo trazar) — para features interactivas que
    asocian feedback 👍/👎 a la llamada. Mismo contrato: NUNCA levanta."""
    try:
        return _completar(tarea, system=system, user=user, usuario=usuario, detalle=detalle)
    except Exception as e:  # cinturón: el contrato es no propagar JAMÁS
        logger.warning("core.ai: fallo inesperado en %s: %s: %s", tarea, type(e).__name__, e)
        return None, None


def _completar(
    tarea: str, *, system: str, user: str, usuario: str | None, detalle: str | None = None
) -> tuple[str | None, int | None]:
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return None, None  # gateway apagado — sin traza (sería ruido en cada corrida)
    cfg = _config(tarea)
    modelo = _modelo(cfg)
    motivo = motivo_presupuesto(usuario)
    if motivo:
        _trazar(tarea, modelo, usuario, None, None, None, False,
                f"presupuesto diario agotado ({motivo})", detalle=detalle)
        return None, None

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
            _trazar(tarea, modelo, usuario, None, None, latencia_ms, False, err, detalle=detalle)
            return None, None
        try:
            data = resp.json()
            texto = (data["choices"][0]["message"]["content"] or "").strip()
            usage = data.get("usage") or {}
        except Exception as e:
            _trazar(tarea, modelo, usuario, None, None, latencia_ms, False,
                    f"respuesta inparseable: {e}", detalle=detalle)
            return None, None
        traza_id = _trazar(tarea, modelo, usuario, usage.get("prompt_tokens"),
                           usage.get("completion_tokens"), latencia_ms, bool(texto),
                           None if texto else "respuesta vacía",
                           detalle=detalle, respuesta=texto or None)
        return (texto or None), traza_id

    latencia_ms = int((time.perf_counter() - t0) * 1000)
    logger.warning("core.ai %s falló tras reintentos: %s", tarea, ultimo_error)
    _trazar(tarea, modelo, usuario, None, None, latencia_ms, False, ultimo_error, detalle=detalle)
    return None, None
