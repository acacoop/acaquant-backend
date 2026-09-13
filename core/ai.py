"""core/ai.py — EL PORTERO DE LA IA.

Toda llamada a un modelo del sistema pasa por acá. `core/llm.py` sabe CÓMO
hablarle al proveedor; este archivo decide QUÉ se le permite y lo anota.

Hace tres cosas, y ninguna la debería resolver cada feature por su cuenta:

1. TAREAS REGISTRADAS. Nadie llama al modelo «como quiera»: dice qué tarea es
   y de esa fila salen el proveedor, el modelo, el tope de respuesta y el
   tiempo de espera. Eso se decide en un solo lugar.

2. EL LIBRO DE LLAMADAS. Cada llamada, salga bien o mal, deja una fila en
   `ia.llamadas`: qué tarea, qué modelo, quién, cuántos tokens, cuánto tardó y
   cuánto pegó en el caché. Sin registro no hay forma de saber qué se gastó.

3. RUTEO SEGURO. Una tarea marcada `datos: "negocio"` sólo puede correr en un
   proveedor que se comprometió a no entrenar con lo que le mandamos. Si no,
   el gateway NIEGA la llamada.

⚠️ **YA NO HAY PRESUPUESTO DIARIO.** Había un tope de tokens por día, global y
por persona, con su tabla y su pantalla. Se sacó entero por decisión del user:
era un mecanismo que nadie miraba y que le pedía una consulta a la base a cada
llamada. Lo único que hoy acota un gasto en bucle es el techo de vueltas del
ciclo (`asistente/ciclo.py::MAX_VUELTAS`) y, para el agente, `AGENTE_REDACTA=0`.
Si algún día vuelve, que vuelva como UNA constante en `config.py`, no como una
tabla editable con precedencia de tres niveles.

⚠️ REGLA PARA SUMAR UNA TAREA: primero contestar **quién mira su salida**. Las
features de IA que murieron en agosto no murieron por bugs — murieron porque
nadie leía lo que producían, y como no fallaban, nadie lo notaba. La historia
está en `docs/AGENT.md` §0.k.

⚠️ CONTRATO: `completar()` NUNCA levanta una excepción. Devuelve el texto o
None. El que llama SIEMPRE tiene su camino sin IA.

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


# Cómo se llama en `ia.config` la elección de modelo. ⚠️ La clave lleva el
# PROVEEDOR adentro, y no es un detalle: un nombre de modelo sólo existe para su
# proveedor. Con una clave por rol a secas (`modelo_pro`), elegir un modelo de
# OpenAI en la pantalla le cambiaría el modelo también a `agente_texto`, que
# corre contra DeepSeek — y ese nombre allá no existe, así que el agente se
# quedaría sin texto todas las noches por un cambio hecho en otra pantalla.
CLAVE_MODELO = "modelo:{proveedor}:{tier}"


def _modelo(cfg: dict) -> str:
    """El modelo que le toca a una tarea. El código pide un ROL (`flash`/`pro`)
    y acá se resuelve a un nombre concreto: primero lo que se haya elegido en la
    tab LAB (`ia.config`), y si no hay nada, el default de `core/llm.py`."""
    tier = cfg.get("tier", "flash")
    prov = _proveedor(cfg)
    elegido = ajustes().get(CLAVE_MODELO.format(proveedor=prov, tier=tier))
    return elegido or llm.modelo(tier, prov)


def modelo_de(tarea: str) -> str:
    """Con qué modelo corre HOY una tarea. Lo usa la pantalla del panel para no
    tener que replicar la precedencia (elección > env > default) por su cuenta:
    replicarla sería dos versiones de la misma regla sin árbitro."""
    return _modelo(_config(tarea))


def proveedor_de(tarea: str) -> str:
    """Qué proveedor le toca a una tarea."""
    return _proveedor(_config(tarea))


def _ruteo_seguro(cfg: dict) -> bool:
    """¿Esta tarea puede salir hacia el proveedor que le toca?

    Una tarea que ve datos de la empresa sólo puede ir a un proveedor que se
    comprometió a no entrenar con ellos. Si no, se niega la llamada.

    Existe porque sin esto la garantía dependía de que el que llama pasara el
    nombre de tarea correcto: una tarea de negocio mal ruteada mandaba los
    números de la empresa justo al proveedor que este ruteo evita.

    ⚠️ **HOY ESTÁ AFLOJADO POR `config.IA_PERMITE_PROVEEDOR_QUE_ENTRENA`**, que
    es donde está escrito el motivo entero y cuándo hay que volverlo atrás. El
    mecanismo queda igual: con esa constante en False, este portazo se reactiva
    completo sin tocar nada más.
    """
    if cfg.get("datos") != "negocio":
        return True
    prov = _proveedor(cfg)
    if llm.no_entrena(prov):
        return True

    from config import IA_PERMITE_PROVEEDOR_QUE_ENTRENA

    if IA_PERMITE_PROVEEDOR_QUE_ENTRENA:
        # WARNING y no silencio: que esté permitido no lo vuelve normal. Cada
        # llamada así deja rastro en el log, además de la fila en `ia.llamadas`.
        logger.warning("core.ai: tarea de negocio hacia %r, que ENTRENA con lo que "
                       "se le manda. Permitido por IA_PERMITE_PROVEEDOR_QUE_ENTRENA.",
                       prov)
        return True
    logger.error("core.ai: RUTEO INSEGURO — tarea de negocio hacia %r (entrena). "
                 "Se NIEGA la llamada.", prov)
    return False


# ── LOS AJUSTES EDITABLES ───────────────────────────────────────────────────
#
# `ia.config` es clave→valor y se puede editar SIN deploy (hoy, desde la tab
# LAB). El orden manda así: lo que diga la tabla, si no la variable de entorno,
# si no el default del código. Se cachea 60 s para no pegarle a la base en cada
# llamada.
#
# Hoy guarda UNA sola cosa: qué modelo cumple cada rol (`modelo_flash`,
# `modelo_pro`). Los nombres de modelo cambian cada pocos meses y el código no
# tiene que enterarse — pide un ROL, nunca un nombre.

_CONFIG_DB_TTL_S = 60
_config_db_cache: dict = {"ts": 0.0, "valores": {}}


def ajustes() -> dict[str, str]:
    """Lo que hay en `ia.config`, cacheado. Si la base no responde devuelve lo
    último conocido: un ajuste que no se pudo leer no puede cortar una llamada."""
    ahora = time.monotonic()
    if ahora - _config_db_cache["ts"] < _CONFIG_DB_TTL_S:
        return _config_db_cache["valores"]
    valores = _config_db_cache["valores"]
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT clave, valor FROM ia.config")
            valores = {r[0]: r[1] for r in cur.fetchall()}
    except Exception as e:
        logger.warning("core.ai: no pude leer ia.config (%s) — uso env/default", e)
    _config_db_cache.update(ts=ahora, valores=valores)
    return valores


def olvidar_ajustes() -> None:
    """Tira el caché. Lo llama quien acaba de escribir un ajuste, para que el
    cambio se vea ya y no dentro de un minuto."""
    _config_db_cache.update(ts=0.0)


# ── LA TRAZA ────────────────────────────────────────────────────────────────
#
# Una fila por llamada en `ia.llamadas`. Los tres extractos de texto se guardan
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
                "INSERT INTO ia.llamadas (tarea, modelo, usuario, tokens_in, tokens_out,"
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
    ruteo no es seguro, o si el proveedor falló.

    `detalle` es un extracto legible del pedido que queda en el libro de
    llamadas, para poder entender después qué se le preguntó.
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
    `ia.llamadas`, para poder guardarlo junto a lo que se haya hecho con esa
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

    Devuelve `(None, …)` si el gateway se negó (1 o 2). Si la llamada salió,
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
