"""core/ai.py — gateway único de IA.

⚠️⚠️ **HOY NO LO LLAMA NADIE (2026-08-28).** El sistema no tiene una sola feature
de IA: el copiloto se dio de baja el 19/08, el MCP el 28/08 y el destilado del
research —la última tarea— el mismo día. Este módulo y `core/llm.py` se
CONSERVARON a propósito, por decisión del user: es el núcleo que costó construir
y que no conviene rehacer desde cero (transporte, ruteo, presupuesto, traza).

**Antes de escribir la primera tarea nueva, la pregunta es: ¿QUIÉN MIRA SU
SALIDA?** Las tres features de IA que murieron este mes murieron por lo mismo, no
por bugs: nadie leía lo que producían, y como no fallaban, nadie lo notaba. Si la
respuesta es «queda en una tabla», la tarea no va.

Lo que NO sobrevivió y hay que rehacer si hace falta: `api/services/ia_obs.py`
(leía las trazas), el chequeo `ia:gateway` de SALUD (vigilaba el gasto) y
`scripts/smoke_ai.py` (probaba que el gateway andaba). Están en git.

TODA llamada a un LLM del sistema pasa por acá. El gateway resuelve lo que
ninguna feature debería resolver por su cuenta:

- **Tareas registradas** (_TAREAS): cada llamada declara una tarea y de ahí
  salen proveedor, modelo (tier flash/pro), max_tokens y timeout. El PROMPT
  vive en el módulo de la feature (ej. jobs/research_mail.py).
- **Transporte y ruteo**: delegados a `core/llm.py` — el ÚNICO módulo que
  conoce a los proveedores (HTTP, auth, retry, dialecto). Una tarea elige su
  proveedor con la clave `proveedor`; cambiar/agregar uno = tocar SOLO
  core/llm.py. Si el proveedor de una tarea no está configurado, la llamada
  NO se hace y NO cae a otro (fail-closed: caer a otro proveedor mandaría
  datos del negocio justo al que entrena con ellos).
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
  AI_BUDGET_TOKENS_DIA_USUARIO  — tope por usuario/día (default 1.000.000).
  AI_BUDGET_TOKENS_DIA_INVITADO — tope del portal invitado (default 100.000).
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
    # ⚠️ **UNA TAREA EXISTE SOLO SI ALGUIEN LEE SU SALIDA** (regla del user,
    # 2026-08-19: *«si lo usa AV Agent perfecto, si no se elimina»*). El 19/08 se
    # borraron SEIS que no la tenían: `copiloto_vista`/`copiloto_vista_pro` y
    # `asistente_negocio` (el botón CONSULTALE A LA IA, dado de baja),
    # `critico_calidad` (evaluaba conversaciones del copiloto), `triage_incidente`
    # (corría cada 10 minutos contra una tabla que **nadie leía** — ni endpoint ni
    # front), `controles_resumen` (una llamada por día cuyo texto terminaba en un
    # `print()` del log) y `salud_diagnostico` (lo generaba el panel de SALUD, que
    # se fue del front). Antes de sumar una tarea nueva: **quién la mira**.
    #
    # ⚠️ Se fueron también las TRES del AV AGENT viejo (`av_agent_informe`,
    # `av_agent_accion`, `av_agent_error`): AGENT 2.0 no usa IA en ninguna de sus
    # 17 habilidades —`usa_ia` es False en las 17— y los services que las
    # invocaban ya no existen. Quedaba la CONFIG (tier, tokens, timeout) de tres
    # tareas que nadie podía llamar, que es la peor clase de código muerto: hace
    # creer que el agente usa IA cuando no la usa.
    "smoke": {"tier": "flash", "max_tokens": 64, "timeout_s": 30, "thinking": "disabled"},
    # ⚠️ `research_destilar` se fue el 2026-08-28 con su job: era la ÚLTIMA tarea
    # productiva del sistema. Se probó cuatro días (14-17/07), se apagó el 17/07
    # y ninguna pantalla llegó a dibujar su salida. Con ella, `smoke` quedó como
    # la única tarea registrada — y `smoke` no produce nada: existe para probar
    # que el transporte anda.
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


def presupuesto_dia_global() -> int:
    """Tope global de tokens/día del gateway — TECHO DURO del sistema: aunque
    la suma de topes por usuario lo supere, el gasto total del día no lo pasa
    (cada llamada chequea los dos). Fuente única — lo usa el check interno y
    la vista de observabilidad (borrada el 2026-08-28 junto con ia_obs.py)."""
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
