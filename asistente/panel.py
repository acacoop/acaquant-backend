"""El panel del LAB: qué gastamos (ia.llamadas) y con qué modelo corre cada
tarea (ia.config, sin deploy). Capa pura, sin FastAPI. Doc: docs/AvAgentAI.md."""
from __future__ import annotations

import logging

from core import modelos
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# ⚠️ La clave de la elección la define `core/modelos.py`, no este archivo — es él
# quien la lee para resolver qué modelo usar. Repetir acá el formato serían dos
# versiones de la misma regla sin árbitro, y el día que difieran la pantalla
# guardaría en un lugar que el gateway no mira: no falla nada, simplemente no
# tiene efecto.
CLAVE_TAREA = modelos.CLAVE_TAREA
# ── LA TARIFA DE UN MODELO ─────────────────────────────────────────────────
#
# Va en la misma tabla y no en el código porque las tarifas cambian: un precio
# hardcodeado no falla, miente — y encima se usa para decidir.
#
# ⚠️⚠️ **SON TRES PRECIOS, NO DOS, Y EL TERCERO ES EL QUE MÁS PESA.** La entrada
# que pega en el CACHÉ del proveedor cuesta una fracción: en gpt-5.6-luna,
# US$0,02 contra US$0,20 — diez veces menos. Cobrar todo a precio de entrada
# infla la factura justo en la parte que venimos optimizando, y hace que el hit
# rate del caché no se vea en el número que mira una persona.
#
# ⚠️ **Y LOS TRES VAN EN UN SOLO VALOR** (`entrada/cache/salida`), por lo mismo
# que el proveedor y el modelo: una tarifa a medias —entrada cargada, caché no—
# calcularía un costo equivocado sin fallar. Junta, no se puede cargar a medias.
#
# En USD por MILLÓN de tokens.
CLAVE_PRECIO = "precio:{modelo}"

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

def _tarifa(ajustes: dict, modelo: str) -> tuple[float, float, float] | None:
    """`(entrada, entrada_cacheada, salida)` en USD por millón, o None si este
    modelo no tiene tarifa cargada. None NO es cero: es «no sé»."""
    crudo = ajustes.get(CLAVE_PRECIO.format(modelo=modelo))
    if not crudo:
        return None
    try:
        e, c, sa = (float(x) for x in str(crudo).split("/"))
    except (ValueError, TypeError):
        logger.warning("panel: tarifa ilegible para %r: %r", modelo, crudo)
        return None
    return e, c, sa


def costo(tarifa, *, cache_hit: int, cache_miss: int, tokens_in: int,
          tokens_out: int) -> float:
    """Lo que costó, con la tarifa que corresponde a cada pedazo.

    ⚠️ **SI NO SE SABE CUÁNTO PEGÓ EN CACHÉ, SE COBRA TODO A PRECIO LLENO.**
    `cache_hit` y `cache_miss` vienen en cero cuando el proveedor no los informó
    — y cero hit no es lo mismo que «no hubo entrada». Sin esta rama, una
    llamada sin telemetría de caché costaría cero pesos de entrada: el número
    quedaría más lindo y más falso.
    """
    p_in, p_cache, p_out = tarifa
    mirado = cache_hit + cache_miss
    entrada = (cache_hit / 1e6 * p_cache + cache_miss / 1e6 * p_in) if mirado \
        else tokens_in / 1e6 * p_in
    return round(entrada + tokens_out / 1e6 * p_out, 6)


def gasto(dias: int = 30) -> dict:
    """Cuánto se llamó al modelo, en tokens y en plata, agrupado por tarea.

    ⚠️ **LA PLATA SÓLO SALE SI CARGASTE EL PRECIO.** Un modelo sin precio
    declarado devuelve `usd: null` y su nombre en `sin_precio` — no se estima.
    Un costo inventado con una tarifa vieja es peor que no mostrar costo: el
    primero se usa para decidir, el segundo se va a buscar.
    """
    aj = modelos.ajustes()
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

    por_tarea, sin_precio, vistos = [], set(), set()
    tot = {"llamadas": 0, "tokens_in": 0, "tokens_out": 0,
           "cache_hit": 0, "cache_miss": 0, "usd": 0.0}
    for tarea, modelo, n, fallidas, t_in, t_out, c_hit, c_miss, ultima in filas:
        vistos.add(modelo)
        tarifa = _tarifa(aj, modelo)
        usd = None
        if tarifa:
            usd = costo(tarifa, cache_hit=int(c_hit), cache_miss=int(c_miss),
                        tokens_in=int(t_in), tokens_out=int(t_out))
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
        # Las tarifas cargadas, para que la pantalla pueda editarlas sin tener
        # que adivinar cuáles hay. Se listan TODOS los modelos que aparecieron
        # en el libro, con o sin tarifa: uno sin cargar es justamente el que hay
        # que cargar.
        "tarifas": [{
            "modelo": m,
            "entrada": t[0] if t else None,
            "cache": t[1] if t else None,
            "salida": t[2] if t else None,
        } for m, t in sorted((m, _tarifa(aj, m)) for m in vistos)],
    }


def conversacion(sesion: str) -> dict:
    """Cuánto costó UNA conversación: sus llamadas al modelo juntas, en tokens,
    caché y plata. Es el lector del `sesion` de `ia.llamadas` (§0.fl) — la
    columna volvió porque ahora hay quien la mire, en cada respuesta de la tab.

    Mismas reglas que `gasto()`: la plata sólo sale si TODOS los modelos que
    intervinieron tienen tarifa; si a uno le falta, `usd` es None y se dice
    cuál. Un costo a medias se lee como un costo.
    """
    sql = """
        SELECT modelo,
               count(*)                            AS llamadas,
               coalesce(sum(tokens_in), 0)         AS tokens_in,
               coalesce(sum(tokens_out), 0)        AS tokens_out,
               coalesce(sum(cache_hit_tokens), 0)  AS cache_hit,
               coalesce(sum(cache_miss_tokens), 0) AS cache_miss
          FROM ia.llamadas
         WHERE sesion = %(s)s
         GROUP BY modelo
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, {"s": sesion})
            filas = cur.fetchall()
    except Exception as e:
        return {"id": sesion, "error": f"no pude leer la conversación: {type(e).__name__}: {e}"}

    aj = modelos.ajustes()
    tot = {"llamadas": 0, "tokens_in": 0, "tokens_out": 0, "cache_hit": 0, "cache_miss": 0}
    usd, sin_precio = 0.0, []
    for modelo, n, t_in, t_out, c_hit, c_miss in filas:
        for k, v in zip(tot, (n, t_in, t_out, c_hit, c_miss)):
            tot[k] += int(v)
        tarifa = _tarifa(aj, modelo)
        if tarifa:
            usd += costo(tarifa, cache_hit=int(c_hit), cache_miss=int(c_miss),
                         tokens_in=int(t_in), tokens_out=int(t_out))
        else:
            sin_precio.append(modelo)
    mirado = tot["cache_hit"] + tot["cache_miss"]
    return {
        "id": sesion,
        **tot,
        "cache_pct": round(100.0 * tot["cache_hit"] / mirado, 1) if mirado else None,
        "usd": None if sin_precio or not filas else round(usd, 4),
        "sin_precio": sorted(sin_precio),
    }


# ── CON QUÉ MODELO CORREMOS ─────────────────────────────────────────────────

def tareas_y_proveedores() -> dict:
    """Qué corre HOY cada tarea, y qué modelos ofrece cada proveedor.

    ⚠️⚠️ **UNA FILA POR TAREA, NO POR `proveedor × rol`.** Así estaba antes y
    era un desplegable que no hacía nada: el user eligió «deepseek · pro» y el
    asistente siguió andando con openai, porque a esa tarea nunca le tocaba esa
    combinación. **Una fila de la pantalla tiene que ser una cosa que corre.**

    Cada tarea trae `para_que` en criollo: «asistente» no le dice nada a nadie
    que no haya escrito `core/modelos.py`.

    Lo que corre lo resuelve `modelos.ficha_de()`, que aplica la precedencia
    (elegido > declarado > default). Replicarla acá sería repetir la regla en
    dos lados, y cuando se desincronicen la pantalla mostraría un modelo y el
    sistema usaría otro sin que nada falle.
    """
    provs = []
    for p in modelos.proveedores():
        provs.append({
            "proveedor": p,
            "configurado": modelos.configurado(p),
            # Con qué corre cada cosa lo decide quien mira este panel. El código
            # ya no opina sobre proveedores: solo dice si la clave está.
            "usable": modelos.configurado(p),
            "modelos": modelos.disponibles(p) if modelos.configurado(p) else [],
        })
    return {"tareas": [modelos.ficha_de(t) for t in modelos.tareas()], "proveedores": provs}


def probar(modelo: str, *, proveedor: str | None = None,
           exigir_herramienta: bool = True) -> dict:
    """¿Este modelo contesta, y sabe PEDIR una herramienta? Una llamada mínima.
    La lista del proveedor trae nombres, no capacidades: un modelo que ignora
    `tools` dejaría al asistente contestando de memoria sin un solo error."""
    from langchain_core.messages import HumanMessage, SystemMessage

    prov = proveedor or modelos.PROVEEDOR_DEFAULT
    try:
        m = modelos.armar(prov, modelo, max_tokens=64, timeout_s=20)
        if exigir_herramienta:
            m = m.bind_tools(_PRUEBA_HERRAMIENTA)
        msg = m.invoke([SystemMessage(content=_PRUEBA_MENSAJES[0]["content"]),
                        HumanMessage(content=_PRUEBA_MENSAJES[1]["content"])])
    except Exception as e:
        return {"ok": False, "motivo": f"el proveedor rechazó el modelo: {type(e).__name__}: {e}"}
    texto = msg.content if isinstance(msg.content, str) else str(msg.content)
    if not exigir_herramienta:
        if not texto.strip():
            return {"ok": False, "motivo": "contestó vacío"}
        return {"ok": True, "contesto": texto[:80]}
    if not msg.tool_calls:
        return {"ok": False,
                "motivo": ("contestó sin pedir la herramienta: este modelo ignora "
                           "`tools`. Con él, el asistente contestaría de memoria "
                           "en vez de consultar la base.")}
    return {"ok": True, "pidio": msg.tool_calls[0]["name"]}


def elegir_modelo(tarea: str, proveedor: str, modelo: str, *, por: str) -> dict:
    """Deja fijado con qué proveedor y modelo corre UNA tarea. **Prueba antes
    de guardar.**

    ⚠️ La prueba no es un botón que se pueda saltear: vive acá adentro, antes
    del INSERT. Si el modelo no sirve, no se guarda y queda el anterior. Misma
    decisión que `cuenta` en las herramientas — lo que se puede mover de
    «acordate de hacerlo» a «no podés no hacerlo», se mueve.

    Qué se le exige depende de la tarea: si OFRECE herramientas (`usa_herramientas`),
    el modelo tiene que PEDIR una; si no, alcanza con que conteste.
    """
    if tarea not in modelos.tareas():
        return {"ok": False, "error": f"tarea {tarea!r} desconocida (hay: {modelos.tareas()})"}
    modelo = str(modelo or "").strip()
    if not modelo:
        return {"ok": False, "error": "falta el nombre del modelo"}
    if proveedor not in modelos.proveedores():
        return {"ok": False, "error": f"proveedor {proveedor!r} desconocido"}

    ficha = modelos.ficha_de(tarea)
    prueba = probar(modelo, proveedor=proveedor,
                    exigir_herramienta=ficha["usa_herramientas"])
    if not prueba["ok"]:
        return {"ok": False, "error": prueba["motivo"], "probado": modelo,
                "guardado": False}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ia.config (clave, valor, updated_by) VALUES (%s, %s, %s) "
                "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor, "
                "  updated_at = now(), updated_by = EXCLUDED.updated_by",
                (CLAVE_TAREA.format(tarea=tarea), f"{proveedor}/{modelo}", por))
    except Exception as e:
        return {"ok": False, "error": f"no pude guardar: {type(e).__name__}: {e}"}
    # El caché de ajustes vive 60 s: sin esto el cambio no se vería hasta que
    # venza, y parecería que no se guardó.
    modelos.olvidar_ajustes()
    return {"ok": True, "tarea": tarea, "proveedor": proveedor, "modelo": modelo,
            "guardado": True}


def volver_al_default(tarea: str, *, por: str) -> dict:
    """Borra la elección de una tarea: vuelve a lo que declara `core/modelos.py`.

    Existe porque «elegí mal y quiero deshacerlo» no se resuelve eligiendo otra
    cosa: se resuelve sacando la elección. Sin esto, una fila de `ia.config`
    puesta una vez se queda para siempre.
    """
    if tarea not in modelos.tareas():
        return {"ok": False, "error": f"tarea {tarea!r} desconocida"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ia.config WHERE clave = %s",
                        (CLAVE_TAREA.format(tarea=tarea),))
    except Exception as e:
        return {"ok": False, "error": f"no pude borrar: {type(e).__name__}: {e}"}
    modelos.olvidar_ajustes()
    return {"ok": True, "tarea": tarea, **modelos.ficha_de(tarea)}


def poner_precio(modelo: str, *, entrada: float, cache: float, salida: float,
                 por: str) -> dict:
    """La tarifa de un modelo, en USD por MILLÓN de tokens. Los TRES juntos.

    ⚠️ **NO SE PUEDE CARGAR A MEDIAS.** Van en un solo valor porque una tarifa
    con la entrada cargada y el caché en blanco calcularía un costo equivocado
    sin fallar — y encima al revés de lo que uno espera: sobrecobraría justo la
    parte más barata.

    Lo que esto NO modela, y hay que saberlo antes de creerle al número:
      · Las **escrituras de caché** cuestan un poco más que la entrada normal, y
        `ia.llamadas` no distingue una entrada que se cacheó de una que no. El
        total es un piso, corto en esa diferencia.
      · El **contexto largo** cuesta el doble en OpenAI a partir de cierto
        tamaño, y no guardamos el tamaño de contexto de cada llamada.
      · DeepSeek cobra distinto en **horario pico**; acá hay un solo precio.
        Cargá el que de verdad pagás.
    """
    modelo = str(modelo or "").strip()
    if not modelo:
        return {"ok": False, "error": "falta el nombre del modelo"}
    try:
        valores = [float(entrada), float(cache), float(salida)]
    except (TypeError, ValueError):
        return {"ok": False, "error": "los precios tienen que ser números"}
    if any(v < 0 for v in valores):
        return {"ok": False, "error": "un precio no puede ser negativo"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ia.config (clave, valor, updated_by) VALUES (%s, %s, %s) "
                "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor, "
                "  updated_at = now(), updated_by = EXCLUDED.updated_by",
                (CLAVE_PRECIO.format(modelo=modelo),
                 "/".join(str(v) for v in valores), por))
    except Exception as e:
        return {"ok": False, "error": f"no pude guardar: {type(e).__name__}: {e}"}
    modelos.olvidar_ajustes()
    return {"ok": True, "modelo": modelo, "entrada": valores[0],
            "cache": valores[1], "salida": valores[2]}


def vista(dias: int = 30) -> dict:
    """Todo el panel en UN request y con UNA sola noción de «ahora» — el mismo
    criterio que `/api/agente/vista`."""
    return {"gasto": gasto(dias), **tareas_y_proveedores()}
