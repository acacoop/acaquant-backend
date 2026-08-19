"""core/ai.py — gateway único de IA (QuantAI Fase 0, ver docs/QUANTAI.md).

TODA llamada a un LLM del sistema pasa por acá. El gateway resuelve lo que
ninguna feature debería resolver por su cuenta:

- **Tareas registradas** (_TAREAS): cada llamada declara una tarea y de ahí
  salen proveedor, modelo (tier flash/pro), max_tokens y timeout. El PROMPT
  vive en el módulo de la feature (ej. core/ai_resumen.py).
- **Transporte y ruteo**: delegados a `core/llm.py` — el ÚNICO módulo que
  conoce a los proveedores (HTTP, auth, retry, dialecto). Una tarea elige su
  proveedor con la clave `proveedor`; cambiar/agregar uno = tocar SOLO
  core/llm.py. Si el proveedor de una tarea no está configurado, la llamada
  NO se hace y NO cae a otro (fail-closed — ver el ruteo del asistente).
- **Presupuesto diario de tokens** (global y por usuario) contra ia.trazas:
  superado → la llamada se niega y la feature degrada. Kill switch de costos.
- **Reintentos**: 1 retry ante timeout / error de conexión / 5xx. Nunca ante 4xx.
- **Traza**: cada llamada (ok o no) deja una fila en SQL `ia.trazas` (tarea,
  modelo, usuario, tokens in/out, latencia, éxito/fallo) — el "job_runs" de la
  IA. Best-effort: si la DB no responde, la llamada sigue igual.

CONTRATO (mismo que core/notify.py): completar() NUNCA propaga excepción.
Devuelve el texto o None; el caller SIEMPRE tiene su camino determinista
(regla de oro 4 del roadmap: todo degrada con gracia).

Env vars (las del proveedor viven en core/llm.py):
  AI_BUDGET_TOKENS_DIA          — tope global de tokens/día (default 2.000.000).
  AI_BUDGET_TOKENS_DIA_USUARIO  — tope por usuario/día (default 200.000).
"""
from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv

from core import llm

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

logger = logging.getLogger(__name__)

_MAX_ERROR_CHARS = 700  # techo del texto de error que se persiste en la traza

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
    # SALUD (api/services/salud.py): diagnóstico de un chequeo que YA se confirmó
    # como persistente. tier pro + thinking ENABLED, igual que el triage: es
    # diagnóstico, no resumen. Lo que se le pide NO es repetir el error —eso ya está
    # en la evidencia— sino traducirlo a negocio: qué vista queda afectada, qué se
    # puede seguir usando, y qué mirar. max_tokens alto porque el razonamiento cuenta
    # como output (lección del P2: con poco volvía vacía).
    "salud_diagnostico": {"tier": "pro", "max_tokens": 2500, "timeout_s": 120,
                          "thinking": "enabled"},
    # AV AGENT — analista del DIAGNÓSTICO MASIVO (api/services/av_agent_analista.py).
    # tier pro + thinking ENABLED: es análisis de PATRONES sobre decenas de casos,
    # la tarea más pesada de todo el programa. Lo que se le pide NO es repetir los
    # diagnósticos —esos ya los hizo el agente, deterministas— sino lo que ninguna
    # fila individual puede decir: qué causas dominan, cuáles se contradicen entre
    # sí, y cuáles huelen a bug del agente en vez de a dato mal cargado.
    # max_tokens alto porque el razonamiento cuenta como output (lección del P2).
    # ⚠️ **max_tokens 12000 y no 4000** (medido 2026-08-18, `ia.trazas`): con 4000
    # la llamada volvió `out=4000` exacto, `respuesta 0 chars` y `razonamiento
    # 2000` — el modelo gastó TODO el presupuesto de salida razonando y nunca
    # llegó a escribir. Es la misma trampa del P2, pero peor acá: analizar 16
    # casos requiere más razonamiento que diagnosticar un job, así que el techo
    # que alcanzaba allá acá se come la respuesta entera. El razonamiento CUENTA
    # como output: el tope tiene que cubrir pensar Y contestar.
    "av_agent_informe": {"tier": "pro", "max_tokens": 12000, "timeout_s": 240,
                         "thinking": "enabled"},
    # AV AGENT — SUGERIR UN VALOR para un caso que la regla determinista no supo
    # resolver (api/services/av_agent_hacer.py). tier pro: la sugerencia la va a
    # aprobar una persona y después se ESCRIBE en el catálogo, así que un error
    # acá no es una respuesta fea, es un dato mal cargado en producción.
    #
    # thinking DISABLED y max_tokens acotado, al revés que `av_agent_informe`:
    # no es análisis de patrones, es clasificar N nombres contra una lista
    # CERRADA de opciones. El razonamiento cuenta como output, y acá lo que se
    # necesita es la lista de items, no la cadena de pensamiento que la produjo.
    "av_agent_accion": {"tier": "pro", "max_tokens": 4000, "timeout_s": 120,
                        "thinking": "disabled"},
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
    # P7 asistente de negocio (api/services/asistente.py): chat con tools para
    # jefes sobre datos del negocio (SIEMPRE tokenizados por la aduana —
    # core/pii_gateway). Tier flash del proveedor con no-retención; thinking
    # disabled (queremos respuesta, no cadena); max_tokens holgado — lección
    # del P2 (el razonamiento cuenta como output y deja la respuesta vacía).
    #
    # ⚠ PROVEEDOR "openai" (decisión del user 2026-07-21): es la ÚNICA tarea
    # que ve datos del NEGOCIO (aunque sin identidades: la aduana las tacha).
    # Va a un proveedor que NO entrena con datos de API y borra a 30 días, en
    # vez del default barato. Sin la credencial de ESE proveedor la tarea NO
    # corre y NO cae al default — sería mandar los números de la empresa justo
    # a donde este ruteo los quiere evitar. El cableado vive en core/llm.py.
    # `datos: "negocio"` = esta tarea ve números del negocio (aunque sin
    # identidades: la aduana las tacha). La invariante `_ruteo_seguro` EXIGE que
    # una tarea así corra en un proveedor con no_entrena=True: si alguien la
    # ruteara mal, el gateway se NIEGA a correr en vez de confiar en el string.
    "asistente_negocio": {"tier": "flash", "proveedor": "openai",
                          "datos": "negocio",
                          "max_tokens": 3000, "timeout_s": 90,
                          "thinking": "disabled"},
    # CONTROL DE CALIDAD de conversaciones (jobs/ia_calidad.py): toma UN turno
    # (pregunta + respuesta, YA tokenizado — la traza es PII-safe) y devuelve
    # JSON {sospechoso, modo, severidad, nota}. Clasificación barata contra los
    # modos de falla conocidos → flash sin thinking, max_tokens chico.
    "critico_calidad": {"tier": "flash", "max_tokens": 400, "timeout_s": 60,
                        "thinking": "disabled"},
}

_DEFAULT_TAREA = {"tier": "flash", "max_tokens": 800, "timeout_s": 60, "thinking": "disabled"}


def _config(tarea: str) -> dict:
    cfg = _TAREAS.get(tarea)
    if cfg is None:
        logger.warning("core.ai: tarea desconocida %r — uso config default (flash)", tarea)
        cfg = _DEFAULT_TAREA
    return cfg


def _proveedor(cfg: dict) -> str:
    return cfg.get("proveedor") or llm.PROVEEDOR_DEFAULT


def _ruteo_seguro(cfg: dict) -> bool:
    """Invariante de PRIVACIDAD: una tarea que ve datos del negocio
    (`datos == "negocio"`) SOLO puede correr en un proveedor que no entrena.

    Cierra el fail-open que el /ia-review encontró: sin esto, la garantía
    dependía 100% de que el caller pasara el string de tarea correcto — una
    tarea de negocio ruteada mal (o registrada con el proveedor equivocado)
    mandaba los números de la empresa justo al proveedor que el ruteo evita. Con
    esto el gateway se niega a correrla. Las tareas de mercado (datos públicos)
    no llevan la marca → no las afecta."""
    if cfg.get("datos") != "negocio":
        return True
    prov = _proveedor(cfg)
    if llm.no_entrena(prov):
        return True
    logger.error("core.ai: RUTEO INSEGURO — tarea de negocio hacia %r (entrena). "
                 "Se NIEGA la llamada (fail-closed).", prov)
    return False


def _modelo(cfg: dict) -> str:
    override_env = cfg.get("model_env")
    if override_env and os.getenv(override_env):
        return os.environ[override_env]
    return llm.modelo(cfg.get("tier", "flash"), _proveedor(cfg))


def disponible(tarea: str) -> bool:
    """True si el PROVEEDOR de esa tarea tiene credencial. Las features lo
    consultan para apagarse con un mensaje claro en vez de intentar y fallar
    (y para NO caer a otro proveedor: el ruteo es una decisión de privacidad,
    no un balanceo)."""
    return llm.configurado(_proveedor(_config(tarea)))


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


def saldo_proveedor() -> dict | None:
    """Saldo real de la cuenta del proveedor DEFAULT, cacheado 5 min
    (core/llm.py). None si no hay key ni valor previo. Nunca levanta.
    Los proveedores secundarios (ej. el del asistente) no exponen saldo —
    su gasto se sigue por `ia.trazas` y por el panel del proveedor."""
    return llm.saldo_cuenta()


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
        # Invitados del portal www (2026-07-21): CADA email de invitado tiene
        # su propio tope diario, más BAJO que el de la mesa (default 100k) —
        # editable por email como excepción personal en Manager (la clave de
        # arriba pisa este default).
        from core.roles import es_invitado_id

        if es_invitado_id(usuario):
            return int(os.getenv("AI_BUDGET_TOKENS_DIA_INVITADO", "100000"))
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
    historial: list[dict] | None = None,
) -> tuple[str | None, int | None, str]:
    """Function calling (piloto 2026-07-20, ver docs/QUANTAI.md): el modelo puede
    PEDIR datos vía `tools` (schema OpenAI) y `ejecutar(nombre, args) -> str`
    los resuelve en código (resultados compactos, capados). Devuelve
    (texto, traza_id, contexto_tools) — contexto_tools acumula TODO lo que las
    tools devolvieron, para que el caller verifique los números contra eso.
    `historial`: turnos previos [{role, content}] que se insertan entre el
    system y el user (memoria conversacional del caller — ya saneada por él).
    Mismo contrato que completar(): NUNCA levanta; cualquier fallo → (None, None, "")."""
    try:
        return _completar_tools_loop(tarea, system=system, user=user, tools=tools,
                                     ejecutar=ejecutar, usuario=usuario, detalle=detalle,
                                     historial=historial)
    except Exception as e:
        logger.warning("core.ai: fallo inesperado en %s (tools): %s: %s",
                       tarea, type(e).__name__, e)
        return None, None, ""


def _completar_tools_loop(
    tarea: str, *, system: str, user: str, tools: list[dict], ejecutar,
    usuario: str | None, detalle: str | None, historial: list[dict] | None = None,
) -> tuple[str | None, int | None, str]:
    import json as _json

    cfg = _config(tarea)
    if not llm.configurado(_proveedor(cfg)) or not _ruteo_seguro(cfg):
        return None, None, ""
    modelo = _modelo(cfg)
    mensajes = [{"role": "system", "content": system}]
    mensajes.extend(historial or [])
    mensajes.append({"role": "user", "content": user})
    contexto_tools: list[str] = []
    traza_id = None

    for ronda in range(1, _MAX_RONDAS_TOOLS + 1):
        motivo = motivo_presupuesto(usuario)
        if motivo:
            _trazar(tarea, modelo, usuario, None, None, None, False,
                    f"presupuesto diario agotado ({motivo})", detalle=detalle)
            return None, None, "\n".join(contexto_tools)
        r = llm.chat(mensajes, modelo=modelo, max_tokens=cfg["max_tokens"],
                     timeout_s=cfg["timeout_s"], thinking=cfg.get("thinking", "disabled"),
                     # 1 retry como el camino simple: desde que TODAS las vistas
                     # del copiloto pasan por acá (tool común del buzón), un
                     # timeout transitorio no puede costar la respuesta
                     tools=tools, reintentos=1, proveedor=_proveedor(cfg))
        if not r.ok:
            _trazar(tarea, modelo, usuario, None, None, r.latencia_ms, False,
                    r.error, detalle=detalle)
            return None, None, "\n".join(contexto_tools)

        if not r.tool_calls:
            texto = r.texto or ""
            traza_id = _trazar(
                tarea, modelo, usuario, r.tokens_in, r.tokens_out, r.latencia_ms,
                bool(texto), None if texto else "respuesta vacía", detalle=detalle,
                respuesta=texto or None, cache_hit=r.cache_hit, cache_miss=r.cache_miss)
            return (texto or None), traza_id, "\n".join(contexto_tools)

        # el modelo pidió datos: el CÓDIGO los resuelve y se le devuelven
        _trazar(tarea, modelo, usuario, r.tokens_in, r.tokens_out, r.latencia_ms,
                True, None, detalle=f"[tools ronda {ronda}] " + (detalle or ""),
                respuesta="; ".join(
                    (c.get("function") or {}).get("name", "?") for c in r.tool_calls),
                cache_hit=r.cache_hit, cache_miss=r.cache_miss)
        mensajes.append(r.mensaje)
        for c in r.tool_calls:
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
    cfg = _config(tarea)
    if not llm.configurado(_proveedor(cfg)) or not _ruteo_seguro(cfg):
        return None, None  # proveedor apagado / ruteo inseguro — sin traza (ruido)
    modelo = _modelo(cfg)
    motivo = motivo_presupuesto(usuario)
    if motivo:
        _trazar(tarea, modelo, usuario, None, None, None, False,
                f"presupuesto diario agotado ({motivo})", detalle=detalle)
        return None, None

    r = llm.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        modelo=modelo, max_tokens=cfg["max_tokens"], timeout_s=cfg["timeout_s"],
        thinking=cfg.get("thinking", "disabled"), reintentos=1,
        proveedor=_proveedor(cfg),
    )
    if not r.ok:
        logger.warning("core.ai %s → %s", tarea, r.error)
        _trazar(tarea, modelo, usuario, None, None, r.latencia_ms, False,
                r.error, detalle=detalle)
        return None, None
    texto = r.texto or ""
    traza_id = _trazar(tarea, modelo, usuario, r.tokens_in, r.tokens_out,
                       r.latencia_ms, bool(texto),
                       None if texto else "respuesta vacía",
                       detalle=detalle, respuesta=texto or None,
                       razonamiento=r.razonamiento,
                       # telemetría del caché de prefijo (~10x más barato el
                       # hit): mide el ahorro real del diseño prefijo-estable
                       cache_hit=r.cache_hit, cache_miss=r.cache_miss)
    return (texto or None), traza_id
