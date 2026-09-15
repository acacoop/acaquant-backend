"""Los modelos de lenguaje: proveedores (LangChain), tareas de ruteo y la puerta
por la que sale cada llamada. Doc: docs/AvAgentAI.md.

Una tarea es un lugar del sistema que le habla a un modelo. Su fila decide
proveedor, modelo y si ve datos del negocio. La elección guardada desde el
panel (`ia.config`, `tarea:<nombre>` = `proveedor/modelo`) pisa el default."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from core.traza import Traza, _texto

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_RAIZ, ".env"))

logger = logging.getLogger(__name__)

PROVEEDOR_DEFAULT = "deepseek"

PROVEEDORES: dict[str, dict] = {
    "deepseek": {
        "key_env": "DEEPSEEK_API_KEY",
        "url_env": "DEEPSEEK_BASE_URL",
        "url_default": "https://api.deepseek.com",
        "modelos": {"flash": ("AI_MODEL_FLASH", "deepseek-v4-flash"),
                    "pro": ("AI_MODEL_PRO", "deepseek-v4-pro")},
        # No se compromete por contrato a no entrenar con lo que se le manda.
        "no_entrena": False,
        # Rechaza `response_format: json_schema` (medido): la respuesta va en prosa.
        "soporta_esquema": False,
    },
    "openai": {
        "key_env": "OPENAI_API_KEY",
        "url_env": "OPENAI_BASE_URL",
        "url_default": "https://api.openai.com/v1",
        "modelos": {"flash": ("AI_MODEL_OPENAI_FLASH", "gpt-5.6-luna"),
                    "pro": ("AI_MODEL_OPENAI_PRO", "gpt-5.6-terra")},
        "no_entrena": True,
        "soporta_esquema": True,
    },
}

# tier: flash (barato) | pro (capaz). datos: "negocio" exige un proveedor que
# no entrena. usa_herramientas: al elegir modelo desde el panel se exige que
# sepa pedir una.
TAREAS: dict[str, dict] = {
    "explicar_error": {"tier": "flash", "max_tokens": 1200, "timeout_s": 60,
                       "para_que": "el botón «explicámelo» de HABILIDADES"},
    "agente_texto": {"tier": "flash", "max_tokens": 400, "timeout_s": 45,
                     "para_que": "el texto de cada aviso del agente en AHORA "
                                 "(lo único que corre SOLO, sin que nadie apriete)"},
    "agente_emisor": {"tier": "pro", "max_tokens": 2000, "timeout_s": 45,
                      "para_que": "las propuestas de emisor en «completar ficha» (ENCONTRÓ)"},
    "asistente_despacho": {"tier": "flash", "max_tokens": 200, "timeout_s": 30,
                           "para_que": "el asistente: decidir qué mundos atienden la pregunta"},
    "asistente_cuenta": {"tier": "pro", "max_tokens": 3000, "timeout_s": 120,
                         "proveedor": "openai", "datos": "negocio", "usa_herramientas": True,
                         "para_que": "el asistente, mundo CUENTA: plata y tenencias de un cliente"},
    "asistente_mercado": {"tier": "flash", "max_tokens": 3000, "timeout_s": 120,
                          "usa_herramientas": True,
                          "para_que": "el asistente, mundo MERCADO: qué hay y cuánto rinde"},
}

CLAVE_TAREA = "tarea:{tarea}"


class SinClave(Exception):
    """Falta la clave del proveedor: la llamada no sale."""


class RuteoInseguro(Exception):
    """Una tarea con datos del negocio no puede salir a un proveedor que entrena."""


# ── proveedores ─────────────────────────────────────────────────────────────


def proveedores() -> list[str]:
    return sorted(PROVEEDORES)


def _cfg(proveedor: str) -> dict:
    if proveedor not in PROVEEDORES:
        raise ValueError(f"proveedor desconocido: {proveedor!r} (hay: {proveedores()})")
    return PROVEEDORES[proveedor]


def clave(proveedor: str) -> str | None:
    return os.getenv(_cfg(proveedor)["key_env"]) or None


def configurado(proveedor: str) -> bool:
    return bool(clave(proveedor))


def no_entrena(proveedor: str) -> bool:
    return bool(_cfg(proveedor)["no_entrena"])


def soporta_esquema(proveedor: str) -> bool:
    return bool(_cfg(proveedor)["soporta_esquema"])


def base_url(proveedor: str) -> str:
    c = _cfg(proveedor)
    return os.getenv(c["url_env"]) or c["url_default"]


def modelo_del_tier(proveedor: str, tier: str) -> str:
    env, default = _cfg(proveedor)["modelos"].get(tier) or _cfg(proveedor)["modelos"]["flash"]
    return os.getenv(env, default)


def disponibles(proveedor: str, *, timeout_s: int = 8) -> list[str]:
    """Los modelos que el proveedor lista hoy (nombres, no capacidades).
    Vacío si no se puede preguntar; nunca levanta."""
    try:
        from openai import OpenAI

        k = clave(proveedor)
        if not k:
            return []
        cli = OpenAI(api_key=k, base_url=base_url(proveedor), timeout=timeout_s, max_retries=0)
        return sorted({m.id for m in cli.models.list() if m.id})
    except Exception as e:
        logger.warning("modelos.disponibles(%s): %s: %s", proveedor, type(e).__name__, e)
        return []


# ── ajustes editables (ia.config) ───────────────────────────────────────────

_AJUSTES_TTL_S = 60
_ajustes_cache: dict = {"ts": 0.0, "valores": {}}


def ajustes() -> dict[str, str]:
    """`ia.config` como dict, cacheado 60 s. Si la base no responde, lo último leído."""
    ahora = time.monotonic()
    if ahora - _ajustes_cache["ts"] < _AJUSTES_TTL_S:
        return _ajustes_cache["valores"]
    valores = _ajustes_cache["valores"]
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT clave, valor FROM ia.config")
            valores = {r[0]: r[1] for r in cur.fetchall()}
    except Exception as e:
        logger.warning("modelos: no pude leer ia.config (%s) — uso env/default", e)
    _ajustes_cache.update(ts=ahora, valores=valores)
    return valores


def olvidar_ajustes() -> None:
    _ajustes_cache.update(ts=0.0)


# ── tareas y ruteo ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Tarea:
    nombre: str
    proveedor: str
    modelo: str
    max_tokens: int
    timeout_s: int
    datos_negocio: bool
    usa_herramientas: bool
    para_que: str
    # True si el modelo salió de una elección guardada en `ia.config`.
    elegido: bool


def tareas() -> list[str]:
    return sorted(TAREAS)


def resolver(tarea: str) -> Tarea:
    """La tarea con su proveedor y modelo resueltos: elección guardada, si no la
    fila declarada, si no el default. Una tarea desconocida no corre."""
    if tarea not in TAREAS:
        raise KeyError(f"tarea desconocida: {tarea!r} (hay: {tareas()})")
    cfg = TAREAS[tarea]
    proveedor = cfg.get("proveedor") or PROVEEDOR_DEFAULT
    modelo = modelo_del_tier(proveedor, cfg.get("tier", "flash"))
    elegido = False
    crudo = ajustes().get(CLAVE_TAREA.format(tarea=tarea))
    if crudo and "/" in crudo:
        p, _, m = crudo.partition("/")
        if p.strip() in PROVEEDORES and m.strip():
            proveedor, modelo, elegido = p.strip(), m.strip(), True
        else:
            logger.warning("modelos: elección guardada inválida para %r: %r", tarea, crudo)
    return Tarea(nombre=tarea, proveedor=proveedor, modelo=modelo,
                 max_tokens=cfg["max_tokens"], timeout_s=cfg["timeout_s"],
                 datos_negocio=cfg.get("datos") == "negocio",
                 usa_herramientas=bool(cfg.get("usa_herramientas")),
                 para_que=cfg.get("para_que", ""), elegido=elegido)


def ficha_de(tarea: str) -> dict:
    t = resolver(tarea)
    return {"tarea": t.nombre, "para_que": t.para_que, "proveedor": t.proveedor,
            "modelo": t.modelo, "elegido": t.elegido, "datos_negocio": t.datos_negocio,
            "usa_herramientas": t.usa_herramientas}


def permitido_salir(tarea: Tarea) -> None:
    """Levanta si la llamada no puede salir: sin clave, o datos del negocio
    hacia un proveedor que entrena (salvo `IA_PERMITE_PROVEEDOR_QUE_ENTRENA`)."""
    if not configurado(tarea.proveedor):
        raise SinClave(f"falta {_cfg(tarea.proveedor)['key_env']} para {tarea.nombre}")
    if tarea.datos_negocio and not no_entrena(tarea.proveedor):
        from config import IA_PERMITE_PROVEEDOR_QUE_ENTRENA

        if not IA_PERMITE_PROVEEDOR_QUE_ENTRENA:
            logger.error("modelos: RUTEO INSEGURO — %s hacia %r (entrena). Se niega.",
                         tarea.nombre, tarea.proveedor)
            raise RuteoInseguro(f"{tarea.nombre} no puede salir a {tarea.proveedor}")
        logger.warning("modelos: %s hacia %r, que entrena. Permitido por "
                       "IA_PERMITE_PROVEEDOR_QUE_ENTRENA.", tarea.nombre, tarea.proveedor)


# ── construir un modelo ─────────────────────────────────────────────────────


def armar(proveedor: str, nombre: str, *, max_tokens: int, timeout_s: int,
          esquema: dict | None = None, callbacks: list | None = None) -> BaseChatModel:
    """Un `ChatModel` de LangChain para ese proveedor y modelo. Reintenta una
    vez ante fallas pasajeras. `esquema` solo viaja si el proveedor lo soporta."""
    comun = {"api_key": clave(proveedor), "timeout": timeout_s, "max_retries": 1,
             "callbacks": list(callbacks or [])}
    kwargs = {}
    if esquema and soporta_esquema(proveedor):
        kwargs["response_format"] = esquema
    if proveedor == "openai":
        from langchain_openai import ChatOpenAI

        # `store=False` siempre: que OpenAI no guarde la conversación de su
        # lado, sin depender del interruptor de la cuenta.
        return ChatOpenAI(model=nombre, base_url=base_url("openai"), store=False,
                          max_completion_tokens=max_tokens, use_responses_api=False,
                          reasoning_effort=os.getenv("AI_OPENAI_REASONING_OFF", "none"),
                          model_kwargs=kwargs, **comun)
    from langchain_deepseek import ChatDeepSeek

    return ChatDeepSeek(model=nombre, api_base=base_url("deepseek"), max_tokens=max_tokens,
                        model_kwargs=kwargs, **comun)


def modelo(tarea: str, *, usuario: str | None = None, sesion: str | None = None,
           esquema: dict | None = None, traza: Traza | None = None) -> BaseChatModel:
    """El modelo de una tarea, listo para invocar, con su traza a `ia.llamadas`.
    Levanta `KeyError`, `SinClave` o `RuteoInseguro` antes de salir."""
    t = resolver(tarea)
    permitido_salir(t)
    tr = traza or Traza(t.nombre, t.modelo, usuario=usuario, sesion=sesion)
    return armar(t.proveedor, t.modelo, max_tokens=t.max_tokens, timeout_s=t.timeout_s,
                 esquema=esquema, callbacks=[tr])


def completar_con_traza(tarea: str, *, system: str, user: str, usuario: str | None = None,
                        detalle: str | None = None) -> tuple[str | None, int | None]:
    """Una pregunta de una sola vuelta. Devuelve (texto, id de la fila en
    `ia.llamadas`); (None, None) si no salió o falló. Nunca levanta."""
    try:
        t = resolver(tarea)
        tr = Traza(t.nombre, t.modelo, usuario=usuario, detalle=detalle)
        m = modelo(tarea, usuario=usuario, traza=tr)
        msg = m.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        return (_texto(msg.content) or None), (tr.ids[-1] if tr.ids else None)
    except Exception as e:
        logger.warning("modelos: %s no contestó: %s: %s", tarea, type(e).__name__, e)
        return None, None


def completar(tarea: str, *, system: str, user: str, usuario: str | None = None,
              detalle: str | None = None) -> str | None:
    return completar_con_traza(tarea, system=system, user=user, usuario=usuario,
                               detalle=detalle)[0]
