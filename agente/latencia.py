"""agente/latencia.py — EL AGENTE DETECTA ENDPOINTS QUE SE PUSIERON LENTOS.

Doc madre: **`docs/AV_AGENT.md`** §0.q.

**POR QUÉ LA PANTALLA DE LATENCIA NO SIRVIÓ** (user, 2026-08-19: *«la verdad
tengo eso en observabilidad, jamás lo usé… ni siquiera se actualiza, puede haber
cosas nuevas y no se entera. Me interesa que el agent pueda detectar en tiempo
real endpoints que estén lentos, pero tiene que ser algo fiable y verdadero, no
tirar por tirar»*).

El diagnóstico es preciso y vale escribirlo, porque es el error de diseño que
este módulo corrige: **un ranking muestra lo LENTO, no lo ANORMAL**. Mirando esa
tabla, arriba de todo aparecen:

    /api/manager/salud/diagnostico   11.724 ms   →  es una llamada al LLM
    /api/back-office/tesoreria/dia      726 ms   →  son 1,6 s de Aunesa medidos

Los dos están **bien**. Son lentos porque hacen algo caro, y van a seguir siendo
los más lentos mañana, y pasado. Un ranking de lentitud es una lista de cosas
inherentemente lentas que **no cambia nunca** — por eso se deja de mirar, y por
eso «no se entera» de nada: no tiene con qué comparar.

LO QUE SÍ ES INFORMACIÓN: LA DEGRADACIÓN
=========================================

Un endpoint que **hoy tarda mucho más que él mismo ayer**. Eso no aparece en un
ranking (puede seguir estando décimo) y es lo único que amerita interrumpir a
alguien. Así que acá cada endpoint se compara **contra sí mismo**, jamás contra
otros: que `/tesoreria/dia` sea más lento que `/me` no es noticia; que
`/tesoreria/dia` esté al triple de su propia normalidad, sí.

LAS CUATRO GUARDAS CONTRA «TIRAR POR TIRAR»
============================================

El user lo pidió explícito, y es la parte difícil: un detector de latencia que
grita seguido es peor que ninguno.

1. **Volumen mínimo.** La media de 3 requests es ruido, no una medición.
2. **Historia mínima.** Sin suficientes horas previas no hay «normal» que
   comparar, y comparar contra dos puntos es adivinar.
3. **Relativo Y absoluto, los dos.** Triplicarse de 10 ms a 30 ms no le importa
   a nadie. Tiene que empeorar en proporción **y** en milisegundos que se noten.
4. **MEDIANA y no promedio** para la línea base: un pico previo subiría la vara
   y taparía justo el problema que se repite.

⚠️ Los números de abajo son **hipótesis sin medir** (REGLA #2 — no tengo acceso
a prod). Están como constantes con nombre para poder moverlas con un dato real:
el primer día que esto cante algo obvio o se quede mudo, se ajustan.
"""
from __future__ import annotations

import logging
from statistics import median

from core.postgres import get_pool

logger = logging.getLogger(__name__)

VENTANA_H = 2          # qué se considera "ahora"
BASE_H = 72            # contra qué se compara (3 días de historia del endpoint)
MIN_REQUESTS = 20      # guarda 1: menos que esto no es una medición
MIN_HORAS_BASE = 6     # guarda 2: sin historia no hay "normal"
FACTOR = 2.5           # guarda 3a: cuántas veces su mediana
MIN_DELTA_MS = 300     # guarda 3b: …y cuántos ms de empeoramiento real

# Los 5xx NO necesitan línea base: un endpoint que rompe está roto, tarde lo que
# tarde. Por eso van con su propio umbral y no pasan por la comparación.
MIN_ERRORES = 3
PCT_ERRORES = 0.05


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _series() -> dict[str, list[dict]]:
    """La serie horaria por endpoint. UNA query — el peaje a Supabase es fijo por
    viaje (~8,5 ms de distancia), así que lo que importa es la cantidad."""
    filas = _q("""
        SELECT endpoint, hora, n, total_ms, max_ms, lentas, errores
        FROM manager.latencia_endpoints
        WHERE hora >= now() - make_interval(hours => %s)
        ORDER BY endpoint, hora
    """, (BASE_H,))
    out: dict[str, list[dict]] = {}
    for f in filas:
        out.setdefault(f["endpoint"], []).append(f)
    return out


def comparar() -> list[dict]:
    """Cada endpoint contra SÍ MISMO. Devuelve solo los que se degradaron.

    No devuelve «el ranking de los más lentos» a propósito: eso ya existía y es
    justamente lo que no servía.
    """
    from datetime import UTC, datetime, timedelta

    corte = datetime.now(UTC) - timedelta(hours=VENTANA_H)
    out = []
    for endpoint, serie in _series().items():
        reciente = [f for f in serie if f["hora"] >= corte]
        previo = [f for f in serie if f["hora"] < corte]
        n_rec = sum(int(f["n"] or 0) for f in reciente)
        if n_rec < MIN_REQUESTS:
            continue                       # guarda 1
        if len(previo) < MIN_HORAS_BASE:
            continue                       # guarda 2

        avg_rec = sum(int(f["total_ms"] or 0) for f in reciente) / n_rec
        # Guarda 4: la MEDIANA de los promedios horarios previos. Un pico de
        # ayer no puede levantar la vara y esconder el problema de hoy.
        base = median([int(f["total_ms"] or 0) / max(int(f["n"] or 0), 1)
                       for f in previo])
        errores = sum(int(f["errores"] or 0) for f in reciente)

        # Guarda 3: los DOS filtros, no uno.
        degradado = (base > 0 and avg_rec >= base * FACTOR
                     and avg_rec - base >= MIN_DELTA_MS)
        roto = errores >= MIN_ERRORES and errores / n_rec >= PCT_ERRORES
        if not (degradado or roto):
            continue
        out.append({
            "endpoint": endpoint, "n": n_rec,
            "avg_ms": round(avg_rec), "base_ms": round(base),
            "veces": round(avg_rec / base, 1) if base else None,
            "max_ms": max(int(f["max_ms"] or 0) for f in reciente),
            "errores": errores,
            "degradado": degradado, "roto": roto,
        })
    out.sort(key=lambda x: -(x["avg_ms"] - x["base_ms"]))
    return out


def detectar_latencia() -> list[dict]:
    """El detector, para el monitor de rueda. **Cero red y una sola query.**

    Corre con los demás detectores live cada pocos minutos, que es lo que el user
    pidió como «en tiempo real». La ventana es de 2 horas y no de minutos porque
    el agregado es por HORA: pedirle a este dato una resolución que no tiene sería
    inventar precisión.
    """
    try:
        casos = comparar()
    except Exception as e:
        logger.warning("agente/latencia: no pude comparar: %s", e)
        return []
    out = []
    for c in casos:
        if c["roto"]:
            out.append(_h("errores", c["endpoint"], "alta",
                          f"{c['errores']} de {c['n']} requests fallaron (5xx)",
                          {"texto": f"en las últimas {VENTANA_H} h. Un endpoint "
                                    f"que rompe está roto tarde lo que tarde: "
                                    f"esto NO pasa por la comparación con su "
                                    f"normalidad.",
                           "errores": c["errores"], "requests": c["n"]}))
        if c["degradado"]:
            out.append(_h("mas_lento", c["endpoint"],
                          "alta" if (c["veces"] or 0) >= 5 else "media",
                          f"tarda {c['veces']}× su normal · {c['avg_ms']} ms "
                          f"contra {c['base_ms']} ms",
                          {"texto": f"{c['n']} requests en las últimas "
                                    f"{VENTANA_H} h, pico {c['max_ms']} ms. La "
                                    f"referencia es la MEDIANA de sus propias "
                                    f"{BASE_H} h previas, no el promedio de "
                                    f"otros endpoints: se compara contra sí mismo.",
                           "avg_ms": c["avg_ms"], "base_ms": c["base_ms"],
                           "veces": c["veces"], "max_ms": c["max_ms"],
                           "requests": c["n"]}))
    return out


def _h(regla: str, endpoint: str, severidad: str, motivo: str,
       evidencia: dict) -> dict:
    """`evidencia` como **dict** (la columna es `jsonb` y el resto del agente la
    lee como objeto), con los números al lado del texto para poder auditar el
    aviso sin volver a consultar."""
    return {"tipo": "latencia", "ticker": endpoint, "regla": regla,
            "severidad": severidad, "motivo": motivo, "evidencia": evidencia}


def como_viene() -> dict:
    """Para el explicador. Lo LENTO y lo DEGRADADO son cosas distintas y viajan
    separadas — confundirlas es exactamente lo que hacía inútil a la pantalla."""
    from datetime import UTC, datetime, timedelta

    corte = datetime.now(UTC) - timedelta(hours=VENTANA_H)
    filas = []
    for endpoint, serie in _series().items():
        rec = [f for f in serie if f["hora"] >= corte]
        n = sum(int(f["n"] or 0) for f in rec)
        if not n:
            continue
        filas.append({
            "endpoint": endpoint, "n": n,
            "avg_ms": round(sum(int(f["total_ms"] or 0) for f in rec) / n),
            "max_ms": max(int(f["max_ms"] or 0) for f in rec),
            "lentas": sum(int(f["lentas"] or 0) for f in rec),
            "errores": sum(int(f["errores"] or 0) for f in rec)})
    filas.sort(key=lambda x: -x["avg_ms"] * x["n"])   # el que más TIEMPO consume
    return {"ventana_h": VENTANA_H, "endpoints": filas[:15],
            "degradados": comparar(),
            "total_requests": sum(f["n"] for f in filas)}
