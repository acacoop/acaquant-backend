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
# razonamiento pesado. `thinking`: "enabled"/"disabled" — VERIFICADO contra la
# doc del proveedor (2026-07-11): los v4 traen thinking DEFAULT ENABLED, por
# eso hay que apagarlo explícito en tareas simples (venía quemando tokens
# invisibles y hasta derramando el razonamiento dentro de la respuesta del
# copiloto). El razonamiento llega aparte en `reasoning_content` → se guarda
# en la traza (debug), nunca se muestra al usuario.
# `model_env` (opcional) = env var que overridea el modelo SOLO para esa tarea.
_TAREAS: dict[str, dict] = {
    "controles_resumen": {"tier": "flash", "max_tokens": 800, "timeout_s": 60,
                          "model_env": "AI_RESUMEN_MODEL", "thinking": "disabled"},
    "smoke": {"tier": "flash", "max_tokens": 64, "timeout_s": 30, "thinking": "disabled"},
    # P2 triage de incidentes (jobs/triage.py): diagnóstico de una falla de job.
    # tier pro + thinking ENABLED a propósito (es diagnóstico — la decisión de
    # QUANTAI.md; cableado 2026-07-11 con el shape verificado). max_tokens ALTO:
    # el razonamiento cuenta como output — con 700 volvía vacía (ia.trazas).
    "triage_incidente": {"tier": "pro", "max_tokens": 2500, "timeout_s": 120,
                         "thinking": "enabled"},
    # P3 copiloto de mesa (api/services/copiloto.py): Q&A sobre los datos de UNA
    # vista de mercado, provistos en el prompt. thinking DISABLED: los datos ya
    # vienen dados y el razonamiento del v4-flash se derramaba en la respuesta
    # (shadow 2026-07-11). max_tokens generoso — lección del P2.
    "copiloto_vista": {"tier": "flash", "max_tokens": 3000, "timeout_s": 60,
                       "thinking": "disabled"},
    # Variante tier PRO del copiloto (decisión user 2026-07-20: "TRADING jamás
    # en flash" — ahí se juega plata en vivo). Mismo contrato que copiloto_vista;
    # la vista elige la tarea vía `tarea` en su entrada del registro. thinking
    # disabled igual (el dato ya viene dado; queremos respuesta, no cadena);
    # timeout más holgado porque el pro es más lento.
    "copiloto_vista_pro": {"tier": "pro", "max_tokens": 3000, "timeout_s": 90,
                           "thinking": "disabled"},
    # P6 memoria de research (jobs/research_mail.py): destila el mail diario de
    # research (prosa larga) a JSON {resumen, temas, hechos} para inyectarlo
    # barato como contexto. Extracción, no razonamiento → flash sin thinking;
    # max_tokens holgado porque el JSON con ~10 hechos ocupa (lección del P2).
    "research_destilar": {"tier": "flash", "max_tokens": 2000, "timeout_s": 90,
                          "thinking": "disabled"},
}

_DEFAULT_TAREA = {"tier": "flash", "max_tokens": 800, "timeout_s": 60, "thinking": "disabled"}


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


def presupuesto_dia_usuario(usuario: str | None = None) -> int:
    """Tope diario de tokens para UN usuario. Precedencia: excepción personal
    (clave 'budget_dia_usuario:<email>' en ia.config, editable en Manager) >
    tope general (clave 'budget_dia_usuario') > env > default 1M (subido de
    200k el 2026-07-11: el copiloto cuesta ~22k tokens/pregunta medidos)."""
    cfgdb = _config_db()
    if usuario:
        propio = cfgdb.get(f"budget_dia_usuario:{usuario}")
        if propio:
            return propio
    v = cfgdb.get("budget_dia_usuario")
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
        if usuario and del_usuario >= presupuesto_dia_usuario(usuario):
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
_MAX_RAZONAMIENTO_CHARS = 2000  # extracto del reasoning_content (debug, panel IA)


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
    razonamiento: str | None = None,
    cache_hit: int | None = None,
    cache_miss: int | None = None,
) -> int | None:
    """Persiste la traza y devuelve su id (para asociar feedback 👍/👎),
    o None si la DB no respondió — best-effort, nunca corta la llamada.
    `detalle`/`respuesta`/`razonamiento` son extractos legibles para el panel
    de OBSERVABILIDAD (qué se preguntó / qué contestó / cómo razonó), capados."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ia.trazas (tarea, modelo, usuario, tokens_in, tokens_out,"
                " latencia_ms, ok, error, detalle, respuesta, razonamiento,"
                " cache_hit_tokens, cache_miss_tokens)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " RETURNING id",
                (tarea, modelo, usuario, tokens_in, tokens_out, latencia_ms, ok,
                 error[:_MAX_ERROR_CHARS] if error else None,
                 detalle[:_MAX_DETALLE_CHARS] if detalle else None,
                 respuesta[:_MAX_RESPUESTA_CHARS] if respuesta else None,
                 razonamiento[:_MAX_RAZONAMIENTO_CHARS] if razonamiento else None,
                 cache_hit, cache_miss),
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


_MAX_TOOL_RESULT_CHARS = 4000   # resultados de tools COMPACTOS (canon Anthropic)
_MAX_RONDAS_TOOLS = 4           # techo de idas y vueltas del loop de tools


def completar_con_tools(
    tarea: str,
    *,
    system: str,
    user: str,
    tools: list[dict],
    ejecutar,
    usuario: str | None = None,
    detalle: str | None = None,
) -> tuple[str | None, int | None, str]:
    """Function calling (piloto 2026-07-20, ver docs/QUANTAI.md): el modelo puede
    PEDIR datos vía `tools` (schema OpenAI) y `ejecutar(nombre, args) -> str`
    los resuelve en código (resultados compactos, capados). Devuelve
    (texto, traza_id, contexto_tools) — contexto_tools acumula TODO lo que las
    tools devolvieron, para que el caller verifique los números contra eso.
    Mismo contrato que completar(): NUNCA levanta; cualquier fallo → (None, None, "")."""
    try:
        return _completar_tools_loop(tarea, system=system, user=user, tools=tools,
                                     ejecutar=ejecutar, usuario=usuario, detalle=detalle)
    except Exception as e:
        logger.warning("core.ai: fallo inesperado en %s (tools): %s: %s",
                       tarea, type(e).__name__, e)
        return None, None, ""


def _completar_tools_loop(
    tarea: str, *, system: str, user: str, tools: list[dict], ejecutar,
    usuario: str | None, detalle: str | None,
) -> tuple[str | None, int | None, str]:
    import json as _json

    import requests

    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return None, None, ""
    cfg = _config(tarea)
    modelo = _modelo(cfg)
    url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com") + "/chat/completions"
    mensajes = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    contexto_tools: list[str] = []
    traza_id = None

    for ronda in range(1, _MAX_RONDAS_TOOLS + 1):
        motivo = motivo_presupuesto(usuario)
        if motivo:
            _trazar(tarea, modelo, usuario, None, None, None, False,
                    f"presupuesto diario agotado ({motivo})", detalle=detalle)
            return None, None, "\n".join(contexto_tools)
        body = {
            "model": modelo, "max_tokens": cfg["max_tokens"],
            "thinking": {"type": cfg.get("thinking", "disabled")},
            "messages": mensajes, "tools": tools,
        }
        t0 = time.perf_counter()
        try:
            resp = requests.post(url, headers={"Authorization": f"Bearer {key}"},
                                 json=body, timeout=cfg["timeout_s"])
        except requests.RequestException as e:
            _trazar(tarea, modelo, usuario, None, None, None, False,
                    f"{type(e).__name__}: {e}", detalle=detalle)
            return None, None, "\n".join(contexto_tools)
        latencia_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code != 200:
            _trazar(tarea, modelo, usuario, None, None, latencia_ms, False,
                    f"HTTP {resp.status_code}: {resp.text[:200]}", detalle=detalle)
            return None, None, "\n".join(contexto_tools)
        try:
            data = resp.json()
            msg = data["choices"][0]["message"]
            usage = data.get("usage") or {}
        except Exception as e:
            _trazar(tarea, modelo, usuario, None, None, latencia_ms, False,
                    f"respuesta inparseable: {e}", detalle=detalle)
            return None, None, "\n".join(contexto_tools)

        llamadas = msg.get("tool_calls") or []
        if not llamadas:
            texto = (msg.get("content") or "").strip()
            traza_id = _trazar(
                tarea, modelo, usuario, usage.get("prompt_tokens"),
                usage.get("completion_tokens"), latencia_ms, bool(texto),
                None if texto else "respuesta vacía", detalle=detalle,
                respuesta=texto or None,
                cache_hit=usage.get("prompt_cache_hit_tokens"),
                cache_miss=usage.get("prompt_cache_miss_tokens"))
            return (texto or None), traza_id, "\n".join(contexto_tools)

        # el modelo pidió datos: el CÓDIGO los resuelve y se le devuelven
        _trazar(tarea, modelo, usuario, usage.get("prompt_tokens"),
                usage.get("completion_tokens"), latencia_ms, True, None,
                detalle=f"[tools ronda {ronda}] " + (detalle or ""),
                respuesta="; ".join(
                    (c.get("function") or {}).get("name", "?") for c in llamadas),
                cache_hit=usage.get("prompt_cache_hit_tokens"),
                cache_miss=usage.get("prompt_cache_miss_tokens"))
        mensajes.append(msg)
        for c in llamadas:
            fn = (c.get("function") or {})
            nombre = fn.get("name") or ""
            try:
                args = _json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            try:
                resultado = str(ejecutar(nombre, args))[:_MAX_TOOL_RESULT_CHARS]
            except Exception as e:
                resultado = f"error de la herramienta: {type(e).__name__}: {e}"
            contexto_tools.append(f"[tool {nombre}({_json.dumps(args, ensure_ascii=False)})] "
                                  f"{resultado}")
            mensajes.append({"role": "tool", "tool_call_id": c.get("id"),
                             "content": resultado})

    # se agotaron las rondas con el modelo todavía pidiendo tools
    _trazar(tarea, modelo, usuario, None, None, None, False,
            f"tools: {_MAX_RONDAS_TOOLS} rondas sin respuesta final", detalle=detalle)
    return None, None, "\n".join(contexto_tools)


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
        # shape verificado contra la doc del proveedor (2026-07-11): default es
        # "enabled" → hay que mandar el switch SIEMPRE, explícito por tarea.
        "thinking": {"type": cfg.get("thinking", "disabled")},
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
            msg = data["choices"][0]["message"]
            texto = (msg.get("content") or "").strip()
            razonamiento = (msg.get("reasoning_content") or "").strip() or None
            usage = data.get("usage") or {}
        except Exception as e:
            _trazar(tarea, modelo, usuario, None, None, latencia_ms, False,
                    f"respuesta inparseable: {e}", detalle=detalle)
            return None, None
        traza_id = _trazar(tarea, modelo, usuario, usage.get("prompt_tokens"),
                           usage.get("completion_tokens"), latencia_ms, bool(texto),
                           None if texto else "respuesta vacía",
                           detalle=detalle, respuesta=texto or None,
                           razonamiento=razonamiento,
                           # telemetría del caché de prefijo (~10x más barato el
                           # hit): mide el ahorro real del diseño prefijo-estable
                           cache_hit=usage.get("prompt_cache_hit_tokens"),
                           cache_miss=usage.get("prompt_cache_miss_tokens"))
        return (texto or None), traza_id

    latencia_ms = int((time.perf_counter() - t0) * 1000)
    logger.warning("core.ai %s falló tras reintentos: %s", tarea, ultimo_error)
    _trazar(tarea, modelo, usuario, None, None, latencia_ms, False, ultimo_error, detalle=detalle)
    return None, None
