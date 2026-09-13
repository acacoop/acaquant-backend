"""asistente/panel.py — LO QUE SE VE Y SE TOCA DESDE LA TAB LAB.

Dos cosas, y ninguna es el ciclo:

  1. **Qué gastamos** — se lee `ia.llamadas`, el libro donde `core/ai.py` anota
     cada vez que el sistema le habla a un modelo. Ese libro existía hace
     meses y no lo miraba nadie.
  2. **Con qué modelo corremos** — se elige acá y se guarda en `ia.config`, que
     `core/ai.py` lee con precedencia sobre el default del código. Sin deploy y
     sin entrar al Droplet.

⚠️ **EL CÓDIGO PIDE UN ROL, NUNCA UN NOMBRE.** Las tareas declaran `tier:
flash|pro`; qué modelo cumple cada rol se decide desde la pantalla. Los nombres
de modelo cambian cada pocos meses y el repo no tiene por qué enterarse.

Capa pura: sin FastAPI. El router es `api/routers/agente.py`.
"""
from __future__ import annotations

import logging

from asistente.ciclo import TAREA
from core import ai, llm
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Los dos roles que el código sabe pedir.
ROLES = ("flash", "pro")

# ⚠️ La clave de la elección la define `core/ai.py`, no este archivo — es él
# quien la lee para resolver qué modelo usar. Repetir acá el formato serían dos
# versiones de la misma regla sin árbitro, y el día que difieran la pantalla
# guardaría en un lugar que el gateway no mira: no falla nada, simplemente no
# tiene efecto. Lleva el PROVEEDOR adentro porque un nombre de modelo sólo
# existe para su proveedor.
CLAVE_MODELO = ai.CLAVE_MODELO
# Y el precio, para poder mostrar plata. Va en la misma tabla y no en el código
# porque las tarifas cambian: un precio hardcodeado no falla, miente.
# Se guarda en USD por MILLÓN de tokens.
CLAVE_PRECIO_IN = "precio_in:{modelo}"
CLAVE_PRECIO_OUT = "precio_out:{modelo}"

# Qué le preguntamos a un modelo para saber si sabe usar herramientas. Es una
# herramienta de mentira y una pregunta que obliga a usarla.
_PRUEBA_HERRAMIENTA = [{
    "type": "function",
    "function": {
        "name": "decir_la_hora",
        "description": "Devuelve la hora actual. Usala SIEMPRE que te pregunten la hora.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]
_PRUEBA_MENSAJES = [
    {"role": "system", "content": "Usás las herramientas que tenés disponibles."},
    {"role": "user", "content": "¿Qué hora es?"},
]


# ── QUÉ GASTAMOS ────────────────────────────────────────────────────────────

def _precio(ajustes: dict, modelo: str, salida: bool) -> float | None:
    clave = (CLAVE_PRECIO_OUT if salida else CLAVE_PRECIO_IN).format(modelo=modelo)
    try:
        return float(ajustes[clave])
    except (KeyError, TypeError, ValueError):
        return None


def gasto(dias: int = 30) -> dict:
    """Cuánto se llamó al modelo, en tokens y en plata, agrupado por tarea.

    ⚠️ **LA PLATA SÓLO SALE SI CARGASTE EL PRECIO.** Un modelo sin precio
    declarado devuelve `usd: null` y su nombre en `sin_precio` — no se estima.
    Un costo inventado con una tarifa vieja es peor que no mostrar costo: el
    primero se usa para decidir, el segundo se va a buscar.
    """
    aj = ai.ajustes()
    sql = """
        SELECT tarea, modelo,
               count(*)                                   AS llamadas,
               count(*) FILTER (WHERE NOT ok)              AS fallidas,
               coalesce(sum(tokens_in), 0)                 AS tokens_in,
               coalesce(sum(tokens_out), 0)                AS tokens_out,
               coalesce(sum(cache_hit_tokens), 0)          AS cache_hit,
               coalesce(sum(cache_miss_tokens), 0)         AS cache_miss,
               max(ts)                                     AS ultima
          FROM ia.llamadas
         WHERE ts >= now() - make_interval(days => %(d)s)
         GROUP BY tarea, modelo
         ORDER BY 5 DESC
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, {"d": max(1, int(dias))})
            filas = cur.fetchall()
            cur.execute(
                "SELECT count(*), coalesce(sum(tokens_in + tokens_out), 0) "
                "FROM ia.llamadas WHERE ts >= date_trunc('day', now())")
            hoy_n, hoy_tok = cur.fetchone()
    except Exception as e:
        return {"error": f"no pude leer el libro de llamadas: {type(e).__name__}: {e}"}

    por_tarea, sin_precio = [], set()
    tot = {"llamadas": 0, "tokens_in": 0, "tokens_out": 0,
           "cache_hit": 0, "cache_miss": 0, "usd": 0.0}
    for tarea, modelo, n, fallidas, t_in, t_out, c_hit, c_miss, ultima in filas:
        p_in, p_out = _precio(aj, modelo, False), _precio(aj, modelo, True)
        usd = None
        if p_in is not None and p_out is not None:
            usd = round(t_in / 1e6 * p_in + t_out / 1e6 * p_out, 4)
            tot["usd"] += usd
        else:
            sin_precio.add(modelo)
        por_tarea.append({
            "tarea": tarea, "modelo": modelo, "llamadas": int(n),
            "fallidas": int(fallidas), "tokens_in": int(t_in), "tokens_out": int(t_out),
            "cache_hit": int(c_hit), "cache_miss": int(c_miss),
            "usd": usd, "ultima": ultima.isoformat() if ultima else None,
        })
        for k, v in (("llamadas", n), ("tokens_in", t_in), ("tokens_out", t_out),
                     ("cache_hit", c_hit), ("cache_miss", c_miss)):
            tot[k] += int(v)

    # El hit rate: de todo lo que ENTRÓ, cuánto salió del caché del proveedor.
    # Es el número que dice si el diseño "lo fijo adelante, lo variable atrás"
    # está funcionando. El caché cuesta una fracción, así que subirlo es la
    # palanca más barata que hay.
    mirado = tot["cache_hit"] + tot["cache_miss"]
    tot["usd"] = round(tot["usd"], 4) if tot["usd"] else None
    return {
        "dias": int(dias),
        "total": tot,
        "hoy": {"llamadas": int(hoy_n or 0), "tokens": int(hoy_tok or 0)},
        "cache_pct": round(100.0 * tot["cache_hit"] / mirado, 1) if mirado else None,
        "por_tarea": por_tarea,
        # Los modelos a los que les falta la tarifa. Sin esto, "usd: null" se
        # lee como "no gastó".
        "sin_precio": sorted(sin_precio),
    }


# ── CON QUÉ MODELO CORREMOS ─────────────────────────────────────────────────

def modelos() -> dict:
    """Qué modelo cumple cada rol hoy, y qué ofrece cada proveedor.

    La lista sale del proveedor en vivo (`llm.disponibles`).

    ⚠️ **UN PROVEEDOR QUE ENTRENA SE PUEDE ELEGIR, PERO SE AVISA.** Hoy
    `config.IA_PERMITE_PROVEEDOR_QUE_ENTRENA` está en True por decisión del user
    (ver el motivo entero ahí). Entonces DeepSeek es elegible — y aun así viaja
    su `aviso`, para que el que lo elige sepa qué está eligiendo. Un permiso que
    no se explica se vuelve un default que nadie recuerda haber decidido.
    """
    from config import IA_PERMITE_PROVEEDOR_QUE_ENTRENA

    aj = ai.ajustes()
    provs = []
    for p in llm.proveedores():
        seguro = llm.no_entrena(p)
        provs.append({
            "proveedor": p,
            "configurado": llm.configurado(p),
            "usable": seguro or IA_PERMITE_PROVEEDOR_QUE_ENTRENA,
            # ⚠️ `aviso` NO es `motivo`: antes decía por qué NO se podía usar;
            # ahora dice qué implica usarlo. El dato es el mismo y la
            # consecuencia cambió, así que cambió el nombre — un campo que dice
            # una cosa y significa otra es cómo se leen mal las pantallas.
            "aviso": None if seguro else (
                "entrena con lo que se le manda: los datos de las cuentas "
                "habilitadas pueden quedar en un modelo de un tercero"),
            "modelos": llm.disponibles(p) if llm.configurado(p) else [],
            "roles": {rol: {
                "elegido": aj.get(CLAVE_MODELO.format(proveedor=p, tier=rol)),
                "default": llm.modelo(rol, p),
            } for rol in ROLES},
        })
    # Con qué corre HOY el asistente. Sale de `core/ai.py`, que es quien aplica
    # la precedencia (elección > env > default): replicarla acá sería repetir la
    # regla en dos lados.
    return {
        "proveedores": provs,
        "asistente": {"tarea": TAREA, "proveedor": ai.proveedor_de(TAREA),
                      "modelo": ai.modelo_de(TAREA)},
    }


def probar(modelo: str, *, proveedor: str | None = None) -> dict:
    """¿Este modelo sabe PEDIR una herramienta? Una llamada mínima y barata.

    ⚠️ **POR QUÉ ESTO EXISTE.** La lista del proveedor trae nombres, no
    capacidades: adentro hay modelos que no son de chat (embeddings, audio) y
    modelos de chat viejos que IGNORAN el campo `tools`. Si elegís uno de esos,
    el asistente deja de consultar la base y **empieza a contestar de memoria,
    inventando** — con el mismo tono de siempre, sin un error, sin un aviso.

    Es el peor modo de falla que puede tener esta app, y una llamada de dos
    segundos lo vuelve imposible.
    """
    # 20 s: la prueba viaja adentro del POST de guardar, y el cliente aborta a
    # los 25. Un modelo que tarda más de 20 s en contestar "¿qué hora es?" no es
    # un modelo que quieras para esto.
    r = llm.chat(_PRUEBA_MENSAJES, modelo=modelo, max_tokens=64, timeout_s=20,
                 thinking="disabled", proveedor=proveedor,
                 herramientas=_PRUEBA_HERRAMIENTA)
    if not r.ok:
        return {"ok": False, "motivo": f"el proveedor rechazó el modelo: {r.error}"}
    if not r.pedidos:
        return {"ok": False,
                "motivo": ("contestó sin pedir la herramienta: este modelo ignora "
                           "`tools`. Con él, el asistente contestaría de memoria "
                           "en vez de consultar la base.")}
    return {"ok": True, "pidio": r.pedidos[0].get("nombre")}


def elegir_modelo(proveedor: str, rol: str, modelo: str, *, por: str) -> dict:
    """Deja fijado qué modelo cumple un rol. **Prueba antes de guardar.**

    ⚠️ La prueba NO es un botón que se puede saltear: es parte de guardar. Si el
    modelo no sabe pedir una herramienta, no se guarda y queda el anterior. Es
    la misma decisión que con `cuenta` en las herramientas — lo que se puede
    mover de «acordate de hacerlo» a «no podés no hacerlo», se mueve.
    """
    if rol not in ROLES:
        return {"ok": False, "error": f"rol {rol!r} desconocido (hay: {list(ROLES)})"}
    modelo = str(modelo or "").strip()
    if not modelo:
        return {"ok": False, "error": "falta el nombre del modelo"}

    if proveedor not in llm.proveedores():
        return {"ok": False, "error": f"proveedor {proveedor!r} desconocido"}
    # ⚠️ El MISMO criterio que aplica el gateway en cada llamada, aplicado
    # también acá y leyendo la MISMA constante: dos reglas para lo mismo se
    # desincronizan, y el día que pase la pantalla dejaría elegir algo que
    # después el gateway rechaza en cada pregunta, sin que nadie entienda por qué.
    from config import IA_PERMITE_PROVEEDOR_QUE_ENTRENA

    if not llm.no_entrena(proveedor) and not IA_PERMITE_PROVEEDOR_QUE_ENTRENA:
        return {"ok": False, "error": (
            f"{proveedor} puede entrenar con lo que se le manda: no puede correr "
            "tareas que ven datos del negocio "
            "(config.IA_PERMITE_PROVEEDOR_QUE_ENTRENA)")}

    prueba = probar(modelo, proveedor=proveedor)
    if not prueba["ok"]:
        return {"ok": False, "error": prueba["motivo"], "probado": modelo,
                "guardado": False}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ia.config (clave, valor, updated_by) VALUES (%s, %s, %s) "
                "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor, "
                "  updated_at = now(), updated_by = EXCLUDED.updated_by",
                (CLAVE_MODELO.format(proveedor=proveedor, tier=rol), modelo, por))
    except Exception as e:
        return {"ok": False, "error": f"no pude guardar: {type(e).__name__}: {e}"}
    # El caché de ajustes vive 60 s: sin esto el cambio no se vería hasta que
    # venza, y parecería que no se guardó.
    ai.olvidar_ajustes()
    return {"ok": True, "proveedor": proveedor, "rol": rol, "modelo": modelo,
            "guardado": True}


def poner_precio(modelo: str, *, entrada: float, salida: float, por: str) -> dict:
    """La tarifa de un modelo, en USD por MILLÓN de tokens. Va en `ia.config` y
    no en el código porque las tarifas cambian — y un precio viejo hardcodeado
    no falla: miente, y encima se usa para decidir."""
    modelo = str(modelo or "").strip()
    if not modelo:
        return {"ok": False, "error": "falta el nombre del modelo"}
    try:
        pares = [(CLAVE_PRECIO_IN.format(modelo=modelo), str(float(entrada))),
                 (CLAVE_PRECIO_OUT.format(modelo=modelo), str(float(salida)))]
    except (TypeError, ValueError):
        return {"ok": False, "error": "los precios tienen que ser números"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            for clave, valor in pares:
                cur.execute(
                    "INSERT INTO ia.config (clave, valor, updated_by) VALUES (%s, %s, %s) "
                    "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor, "
                    "  updated_at = now(), updated_by = EXCLUDED.updated_by",
                    (clave, valor, por))
    except Exception as e:
        return {"ok": False, "error": f"no pude guardar: {type(e).__name__}: {e}"}
    ai.olvidar_ajustes()
    return {"ok": True, "modelo": modelo, "entrada": entrada, "salida": salida}


def vista(dias: int = 30) -> dict:
    """Todo el panel en UN request y con UNA sola noción de «ahora» — el mismo
    criterio que `/api/agente/vista`."""
    return {"gasto": gasto(dias), **modelos()}
