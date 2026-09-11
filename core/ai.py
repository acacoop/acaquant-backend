"""core/ai.py — EL PORTERO DE LA IA.

Toda llamada a un modelo del sistema pasa por acá. `core/llm.py` sabe CÓMO
hablarle al proveedor; este archivo decide QUÉ se le permite y lo anota.

Hace cuatro cosas, y ninguna la debería resolver cada feature por su cuenta:

1. TAREAS REGISTRADAS. Nadie llama al modelo «como quiera»: dice qué tarea es
   y de esa fila salen el proveedor, el modelo, el tope de respuesta y el
   tiempo de espera. Eso se decide en un solo lugar.

2. PRESUPUESTO DIARIO. Hay un tope de tokens por día, global y por persona. Si
   se pasó, la llamada se niega. Es el freno contra una factura sorpresa.

3. TRAZA. Cada llamada, salga bien o mal, deja una fila en `ia.trazas`: qué
   tarea, qué modelo, quién, cuántos tokens, cuánto tardó. Sin registro no hay
   forma de saber qué se gastó ni en qué.

4. RUTEO SEGURO. Una tarea marcada `datos: "negocio"` sólo puede correr en un
   proveedor que se comprometió a no entrenar con lo que le mandamos. Si no,
   el gateway NIEGA la llamada.

⚠️ REGLA PARA SUMAR UNA TAREA: primero contestar **quién mira su salida**. Las
features de IA que murieron en agosto no murieron por bugs — murieron porque
nadie leía lo que producían, y como no fallaban, nadie lo notaba. La historia
está en `docs/AGENT.md` §0.k.

⚠️ CONTRATO: `completar()` NUNCA levanta una excepción. Devuelve el texto o
None. El que llama SIEMPRE tiene su camino sin IA.

Variables de entorno:
  AI_BUDGET_TOKENS_DIA           tope global de tokens por día (2.000.000)
  AI_BUDGET_TOKENS_DIA_USUARIO   tope por persona por día (1.000.000)
  AI_BUDGET_TOKENS_DIA_INVITADO  tope de un invitado del portal www (100.000)
"""
from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv

from core import llm

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_RAIZ, ".env"))

logger = logging.getLogger(__name__)


# ── LAS TAREAS ──────────────────────────────────────────────────────────────
#
# tier      "flash" = barato, para redactar o clasificar · "pro" = caro, para
#           razonar sobre varios datos.
# thinking  "disabled" apaga el razonamiento previo. Va explícito porque los
#           modelos lo traen PRENDIDO de fábrica: sin esto, una tarea simple
#           quema tokens que no se ven.
# datos     "negocio" marca que la tarea ve números de la empresa. Sin la
#           marca, se asume que son datos públicos de mercado.
#
# Cada fila dice QUIÉN MIRA SU SALIDA. Una tarea sin esa respuesta no va.

_TAREAS: dict[str, dict] = {
    # La mira: la tab LAB del modal del AV AGENT. El investigador habla con el
    # proveedor por LangChain y no por `completar()`, así que de esta fila sólo
    # se usan el nombre y el presupuesto — la traza la escribe él con
    # `registrar()`.
    "investigador": {"tier": "pro", "max_tokens": 4000, "timeout_s": 120,
                     "thinking": "disabled"},

    # La mira: el botón «explicámelo» del panel HABILIDADES. Sólo a pedido de
    # una persona, y cacheada por error: el mismo error no se paga dos veces.
    "explicar_error": {"tier": "flash", "max_tokens": 1200, "timeout_s": 60,
                       "thinking": "disabled"},

    # La mira: el texto de cada aviso del AV AGENT en la tab AHORA. Es la única
    # que corre SOLA, sin que nadie apriete, así que trae guardas que las de a
    # pedido no necesitan (tope por pasada, tope de intentos por aviso,
    # validación de la salida y un texto fijo de respaldo si algo falla).
    # 400 tokens porque la salida es una o dos frases: darle más es invitarlo
    # a escribir de más.
    "agente_texto": {"tier": "flash", "max_tokens": 400, "timeout_s": 45,
                     "thinking": "disabled"},

    # La mira: el listado de «completar ficha» en la tab ENCONTRÓ. Propone el
    # emisor de los títulos que ninguna regla puede derivar, eligiendo de una
    # lista cerrada. No escribe nada: una persona confirma.
    # "pro" porque acá no se redacta, se RECONOCE — que «Ciclo Nova Ahorro
    # Plus» es un fondo de IEB no sale de leer el nombre —, y equivocarse
    # escribe un dato en un campo por el que se agrupa plata.
    "agente_emisor": {"tier": "pro", "max_tokens": 2000, "timeout_s": 45,
                      "thinking": "disabled"},

    # La mira: una persona con rol admin, en pantalla, mientras conversa.
    #
    # ⚠️ ES LA PRIMERA TAREA DISTINTA A TODAS LAS DE ARRIBA, en tres cosas:
    #
    #  · `proveedor: openai` — las otras cuatro usan el default (deepseek).
    #  · `datos: negocio`    — ve tenencias, vencimientos y plata de la casa.
    #                          Es la primera que enciende de verdad el ruteo de
    #                          `_ruteo_seguro()`: si alguien la ruteara a un
    #                          proveedor que entrena, el gateway la NIEGA.
    #  · es CONVERSACIONAL   — no entra por `completar()` sino por `conversar()`,
    #                          porque una conversación es una lista de mensajes
    #                          que crece y puede pedir herramientas.
    #
    # "pro" y no "flash": tiene que elegir entre varias herramientas y cruzar
    # datos de dos mundos (lo que tenemos y lo que hay en el mercado). Elegir
    # mal la herramienta es contestar con seguridad sobre otra cosa.
    "asistente": {"tier": "pro", "max_tokens": 3000, "timeout_s": 120,
                  "thinking": "disabled", "proveedor": "openai",
                  "datos": "negocio"},
}

# Si alguien pide una tarea que no está en la tabla, corre igual con esto (y
# queda un warning). Que una tarea sin declarar no rompa nada es a propósito.
_DEFAULT_TAREA = {"tier": "flash", "max_tokens": 800, "timeout_s": 60,
                  "thinking": "disabled"}


def _config(tarea: str) -> dict:
    cfg = _TAREAS.get(tarea)
    if cfg is None:
        logger.warning("core.ai: tarea desconocida %r — uso config default (flash)", tarea)
        cfg = _DEFAULT_TAREA
    return cfg


def _proveedor(cfg: dict) -> str:
    return cfg.get("proveedor") or llm.PROVEEDOR_DEFAULT


def _modelo(cfg: dict) -> str:
    return llm.modelo(cfg.get("tier", "flash"), _proveedor(cfg))


def _ruteo_seguro(cfg: dict) -> bool:
    """¿Esta tarea puede salir hacia el proveedor que le toca?

    Una tarea que ve datos de la empresa sólo puede ir a un proveedor que se
    comprometió a no entrenar con ellos. Si no, se niega la llamada.

    Existe porque sin esto la garantía dependía de que el que llama pasara el
    nombre de tarea correcto: una tarea de negocio mal ruteada mandaba los
    números de la empresa justo al proveedor que este ruteo evita.
    """
    if cfg.get("datos") != "negocio":
        return True
    prov = _proveedor(cfg)
    if llm.no_entrena(prov):
        return True
    logger.error("core.ai: RUTEO INSEGURO — tarea de negocio hacia %r (entrena). "
                 "Se NIEGA la llamada.", prov)
    return False


# ── EL PRESUPUESTO ──────────────────────────────────────────────────────────
#
# Los topes se pueden editar sin deploy desde la tabla `ia.config`. El orden
# manda así: lo que diga la tabla, si no la variable de entorno, si no el
# default del código. Se cachea 60 s para no pegarle a la base en cada llamada.

_CONFIG_DB_TTL_S = 60
_config_db_cache: dict = {"ts": 0.0, "valores": {}}


def _config_db() -> dict:
    ahora = time.monotonic()
    if ahora - _config_db_cache["ts"] < _CONFIG_DB_TTL_S:
        return _config_db_cache["valores"]
    valores = _config_db_cache["valores"]  # si la base no responde, el último conocido
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
    """Techo duro del sistema: aunque la suma de los topes por persona sea
    mayor, el gasto total del día no pasa de acá."""
    v = _config_db().get("budget_dia_global")
    return v if v else int(os.getenv("AI_BUDGET_TOKENS_DIA", "2000000"))


def presupuesto_dia_usuario(usuario: str | None = None) -> int:
    """Tope diario de una persona. Se puede subir o bajar por email desde
    Manager (clave `budget_dia_usuario:<email>` en `ia.config`)."""
    cfgdb = _config_db()
    if usuario:
        propio = cfgdb.get(f"budget_dia_usuario:{usuario}")
        if propio:
            return propio
        # Un invitado del portal www tiene un tope más bajo que la mesa. Hoy
        # ninguna feature de IA le llega, pero si mañana alguna le llega, este
        # default evita que se lleve el tope de un trader sin que nada falle.
        from core.roles import es_invitado_id

        if es_invitado_id(usuario):
            return int(os.getenv("AI_BUDGET_TOKENS_DIA_INVITADO", "100000"))
    v = cfgdb.get("budget_dia_usuario")
    return v if v else int(os.getenv("AI_BUDGET_TOKENS_DIA_USUARIO", "1000000"))


def motivo_presupuesto(usuario: str | None) -> str | None:
    """Qué tope se pasó hoy: "global", "usuario", o None si hay margen.

    Devuelve el motivo y no un sí/no porque «se acabó tu cuota» y «se acabó la
    del sistema» se arreglan distinto, y el que lo lee en pantalla necesita
    saber cuál de las dos es.

    Si la base no responde NO bloquea: esto es control de gastos, no una
    puerta de seguridad.
    """
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
            logger.warning("core.ai: presupuesto diario de %s agotado (%s tokens hoy)",
                           usuario, del_usuario)
            return "usuario"
        return None
    except Exception as e:
        logger.warning("core.ai: no pude chequear el presupuesto (%s) — sigo sin bloquear", e)
        return None


# ── LA TRAZA ────────────────────────────────────────────────────────────────
#
# Una fila por llamada en `ia.trazas`. Los tres extractos de texto se guardan
# recortados: sirven para poder leer después qué se preguntó y qué contestó el
# modelo, que es la única forma de revisar una respuesta que ya se fue.

_MAX_ERROR_CHARS = 700
_MAX_DETALLE_CHARS = 600      # extracto del pedido
_MAX_RESPUESTA_CHARS = 1500   # extracto de lo que contestó
_MAX_RAZONAMIENTO_CHARS = 2000


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
    """Guarda la fila y devuelve su id. Si la base no responde devuelve None y
    la llamada sigue igual: perder la traza es malo, cortar la feature es peor."""
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


def registrar(
    tarea: str,
    *,
    modelo: str,
    usuario: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    latencia_ms: int | None = None,
    ok: bool = True,
    error: str | None = None,
    detalle: str | None = None,
    respuesta: str | None = None,
) -> int | None:
    """Deja la traza de una llamada que este archivo NO hizo.

    Existe para el investigador del lab, que habla con el proveedor por
    LangChain porque necesita un objeto-modelo con herramientas y no una
    función. Sin esto, ese gasto no aparecería en ningún lado.

    No es una puerta trasera: ese caller igual consulta el presupuesto antes de
    arrancar. Lo que esto garantiza es que no haya gasto sin registro.
    """
    return _trazar(tarea, modelo, usuario, tokens_in, tokens_out, latencia_ms,
                   ok, error, detalle=detalle, respuesta=respuesta)


# ── LA LLAMADA ──────────────────────────────────────────────────────────────

def completar(
    tarea: str,
    *,
    system: str,
    user: str,
    usuario: str | None = None,
    detalle: str | None = None,
) -> str | None:
    """Le hace una pregunta al modelo y devuelve el texto, o None.

    Devuelve None —sin levantar nunca— si falta la clave del proveedor, si el
    ruteo no es seguro, si se acabó el presupuesto o si el proveedor falló.

    `detalle` es un extracto legible del pedido que queda en la traza, para
    poder entender después qué se le preguntó.
    """
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
    """Igual que `completar()` pero devuelve además el id de la fila de
    `ia.trazas`, para poder guardarlo junto a lo que se haya hecho con esa
    respuesta y después saber de qué llamada salió. Mismo contrato: no levanta.
    """
    try:
        return _completar(tarea, system=system, user=user, usuario=usuario, detalle=detalle)
    except Exception as e:
        # Cinturón: el contrato es no propagar JAMÁS una excepción.
        logger.warning("core.ai: fallo inesperado en %s: %s: %s", tarea, type(e).__name__, e)
        return None, None


def conversar(
    tarea: str,
    *,
    mensajes: list[dict],
    herramientas: list[dict] | None = None,
    usuario: str | None = None,
    detalle: str | None = None,
) -> tuple[llm.RespuestaLLM | None, int | None]:
    """UNA vuelta de conversación con el modelo. Devuelve (respuesta, id de traza).

    ── SU ROL EN EL CICLO: es la puerta por la que pasa CADA vuelta ──

    El ciclo del asistente llama a esto varias veces por pregunta: una para que
    el modelo decida qué herramienta quiere, otra para que redacte con lo que
    volvió, y así hasta que conteste texto. Esta función no sabe nada de ese
    ciclo — hace una vuelta, la anota, y vuelve.

    Qué revisa antes de dejar salir la llamada, en este orden:
      1. ¿hay clave del proveedor que le toca a la tarea?
      2. ¿el ruteo es seguro? (una tarea de negocio no sale a un proveedor
         que entrena con lo que le mandamos)
      3. ¿queda presupuesto de hoy?

    ⚠️ EL PRESUPUESTO SE MIRA EN CADA VUELTA, no una vez por pregunta. Una
    conversación con herramientas son varias llamadas, y si el techo se toca a
    la mitad, la próxima vuelta no sale. Es lo que evita que una conversación
    que se va de mano gaste sin freno.

    Devuelve `(None, …)` si el gateway se negó (1, 2 o 3). Si la llamada salió,
    devuelve la respuesta aunque el proveedor haya fallado — ahí `ok` es False
    y el motivo está adentro. NUNCA levanta una excepción.
    """
    try:
        return _conversar(tarea, mensajes=mensajes, herramientas=herramientas,
                          usuario=usuario, detalle=detalle)
    except Exception as e:
        # Cinturón: el contrato es no propagar JAMÁS una excepción.
        logger.warning("core.ai: fallo inesperado en %s: %s: %s", tarea, type(e).__name__, e)
        return None, None


def _conversar(
    tarea: str,
    *,
    mensajes: list[dict],
    herramientas: list[dict] | None,
    usuario: str | None,
    detalle: str | None,
) -> tuple[llm.RespuestaLLM | None, int | None]:
    cfg = _config(tarea)
    # Sin clave o con ruteo inseguro no se traza: sería ruido, no un gasto.
    if not llm.configurado(_proveedor(cfg)) or not _ruteo_seguro(cfg):
        return None, None
    modelo = _modelo(cfg)

    motivo = motivo_presupuesto(usuario)
    if motivo:
        # Esto SÍ se traza aunque no haya llamada: que el presupuesto haya
        # frenado algo es justo lo que hay que poder ver después.
        traza_id = _trazar(tarea, modelo, usuario, None, None, None, False,
                           f"presupuesto diario agotado ({motivo})", detalle=detalle)
        return None, traza_id

    r = llm.chat(mensajes, modelo=modelo, max_tokens=cfg["max_tokens"],
                 timeout_s=cfg["timeout_s"], thinking=cfg.get("thinking", "disabled"),
                 reintentos=1, proveedor=_proveedor(cfg), herramientas=herramientas)

    if not r.ok:
        logger.warning("core.ai %s → %s", tarea, r.error)
        traza_id = _trazar(tarea, modelo, usuario, None, None, r.latencia_ms, False,
                           r.error, detalle=detalle)
        return r, traza_id

    # Una vuelta que pide herramientas no trae texto, y eso NO es una respuesta
    # vacía: es el modelo diciendo "todavía no terminé". Por eso la traza se
    # marca ok si vino texto O si vino un pedido.
    texto = r.texto or ""
    hubo_algo = bool(texto) or bool(r.pedidos)
    traza_id = _trazar(tarea, modelo, usuario, r.tokens_in, r.tokens_out,
                       r.latencia_ms, hubo_algo,
                       None if hubo_algo else "respuesta vacía",
                       detalle=detalle, respuesta=texto or None,
                       razonamiento=r.razonamiento,
                       cache_hit=r.cache_hit, cache_miss=r.cache_miss)
    return r, traza_id


def _completar(
    tarea: str, *, system: str, user: str, usuario: str | None, detalle: str | None = None
) -> tuple[str | None, int | None]:
    """Una pregunta suelta ES la conversación más corta que existe: dos
    mensajes y ninguna herramienta. Por eso pasa por la misma puerta."""
    r, traza_id = _conversar(
        tarea,
        mensajes=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        herramientas=None, usuario=usuario, detalle=detalle,
    )
    if r is None or not r.ok:
        return None, None
    return (r.texto or None), traza_id
