"""api/services/av_agent_items.py — EL STORE DE LOS OBJETOS DEL AGENTE.

Doc madre: **`docs/AV_AGENT.md`** §0.bd. El modelo (estados, transiciones,
hitos, `Item`) vive en `core/ciclo.py`; acá está la persistencia y nada más.

QUÉ RESUELVE, Y POR QUÉ ES UNA TABLA Y NO VEINTIDÓS
===================================================

El user (2026-08-21): *«que todo lo del AV Agent esté como objeto; va a ser
siempre el mismo estilo, solo que va a cambiar el TIPO de problema —log, aviso,
etc.— pero cómo van a estar es lo mismo»*.

Y la medición le daba la razón: **22 tablas, 8 formas de decir «resuelto»**.

⚠️ **LO QUE ESTO ARREGLA DE FONDO ES LA MEMORIA.** `av_agent_hallazgos` guarda
una FOTO por corrida: el mismo problema se reescribe entero cada vez, sin
identidad. Por eso aparecía «nuevo» todas las ruedas, por eso perdía que ya lo
habías votado, y por eso el agente parecía no acordarse de nada.

Acá la PK es la `clave` y **no lleva fecha**. Ver el mismo problema mañana no
crea una fila: actualiza la que hay. De ahí salen tres cosas que antes no
existían:

    · `abierto_at` mide antigüedad DE VERDAD (no «desde la última corrida»)
    · `veces` cuenta cuántas ruedas lleva sin resolverse
    · si estaba RESUELTO y reaparece → `volvio`, que **no es lo mismo que nuevo**

CONVIVE CON LO VIEJO
====================

No se migró ninguna tabla: las 22 siguen ahí y `core.ciclo.sin_migrar()` cuenta
las 11 que tienen ciclo propio. Esto es el destino, y cada superficie se mueve
cuando le toca. Migrar de un saque es cómo se rompe un sistema que funciona.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from core import ciclo
from core.postgres import get_pool

logger = logging.getLogger(__name__)

_COLS = ("clave", "tipo", "origen", "sujeto", "regla", "estado", "severidad",
         "veces", "abierto_at", "ultimo_at", "visto_at", "resuelto_at",
         "vuelto_at", "titulo", "afecta", "datos")


def _fila(r) -> ciclo.Item:
    d = dict(zip(_COLS, r, strict=False))
    d["datos"] = d.get("datos") or {}
    return ciclo.Item(**d)


def ver(*, tipo: str, origen: str, sujeto: str, regla: str = "",
        titulo: str = "", afecta: str = "", severidad: str = "media",
        datos: dict | None = None) -> dict:
    """**«Vi esto».** Lo crea si es nuevo, lo actualiza si ya estaba.

    Es el ÚNICO camino de entrada, y es idempotente: un detector puede llamarlo
    en cada corrida sin pensar. Lo que hace de más —y es todo el punto— es
    distinguir tres situaciones que hoy se veían iguales:

        no estaba          → nace `nuevo`
        estaba abierto     → suma `veces`, refresca el título, NO pisa `abierto_at`
        estaba RESUELTO    → **`volvio`**, con `vuelto_at`

    ⚠️ **`abierto_at` no se pisa nunca.** Es lo que convierte «apareció hoy» en
    «lleva 11 días»: sin eso, un problema de hace dos semanas se ve igual de
    urgente que uno de recién, y por eso nada acumulaba antigüedad.

    ⚠️ **El TÍTULO sí se refresca.** El problema es el mismo pero su explicación
    puede mejorar (una firma nueva, una traducción del modelo): guardar la
    primera para siempre sería congelar el peor texto.
    """
    clave = ciclo.clave_de(tipo, origen, sujeto, regla)
    if not clave:
        return {"ok": False, "error": "un item sin tipo ni sujeto no es nada"}
    ahora = datetime.now(UTC)
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mercado.av_agent_items "
                "(clave, tipo, origen, sujeto, regla, estado, severidad, "
                " titulo, afecta, datos, abierto_at, ultimo_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) "
                "ON CONFLICT (clave) DO UPDATE SET "
                # El estado: si estaba resuelto y volvió a aparecer, VOLVIÓ.
                # Se resuelve en SQL —y no leyendo primero— para que dos
                # detectores corriendo a la vez no se pisen.
                "  estado = CASE WHEN mercado.av_agent_items.estado = %s "
                "                THEN %s ELSE mercado.av_agent_items.estado END, "
                "  vuelto_at = CASE WHEN mercado.av_agent_items.estado = %s "
                "                   THEN %s ELSE mercado.av_agent_items.vuelto_at END, "
                # Y si volvió, deja de estar resuelto: si no, el seguimiento
                # seguiría contándole hitos a un arreglo que ya falló.
                "  resuelto_at = CASE WHEN mercado.av_agent_items.estado = %s "
                "                     THEN NULL ELSE mercado.av_agent_items.resuelto_at END, "
                "  veces = mercado.av_agent_items.veces + 1, "
                "  ultimo_at = EXCLUDED.ultimo_at, "
                "  severidad = EXCLUDED.severidad, "
                "  titulo = EXCLUDED.titulo, "
                "  afecta = EXCLUDED.afecta, "
                "  datos = EXCLUDED.datos "
                "RETURNING (xmax = 0) AS nacio, estado, veces, abierto_at",
                (clave, tipo, origen, sujeto, regla, ciclo.NUEVO, severidad,
                 titulo, afecta, json.dumps(datos or {}, ensure_ascii=False,
                                            default=str), ahora, ahora,
                 ciclo.RESUELTO, ciclo.VOLVIO,
                 ciclo.RESUELTO, ahora,
                 ciclo.RESUELTO))
            nacio, estado, veces, abierto = cur.fetchone()
        return {"ok": True, "clave": clave, "nuevo": bool(nacio),
                "estado": estado, "veces": veces,
                "dias_abierto": round(ciclo._dias(abierto), 2)}
    except Exception as e:
        logger.warning("av_agent_items: no pude registrar %s (%s)", clave, e)
        return {"ok": False, "error": str(e)[:200]}


def marcar(clave: str, estado: str, *, por: str = "") -> dict:
    """Mueve un item de estado, **validando la transición**.

    Un salto imposible (`resuelto → en_curso`) se rechaza acá y no se descubre
    tres pantallas después. La marca de tiempo la pone el estado al que va: no
    hay dos fuentes para «cuándo se resolvió».
    """
    estado = (estado or "").strip()
    if estado not in ciclo.ESTADOS:
        return {"ok": False, "error": f"«{estado}» no es un estado"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT estado FROM mercado.av_agent_items "
                        "WHERE clave = %s", (clave,))
            f = cur.fetchone()
            if not f:
                return {"ok": False, "error": "ese item no existe"}
            actual = f[0]
            if actual == estado:
                return {"ok": True, "sin_cambio": True, "estado": estado}
            if not ciclo.puede_pasar(actual, estado):
                return {"ok": False, "error": (
                    f"de «{actual}» no se puede pasar a «{estado}»")}
            ahora = datetime.now(UTC)
            cur.execute(
                "UPDATE mercado.av_agent_items SET estado = %s, "
                "  visto_at    = COALESCE(visto_at, %s), "
                "  resuelto_at = CASE WHEN %s = 'resuelto' THEN %s ELSE resuelto_at END, "
                "  vuelto_at   = CASE WHEN %s = 'volvio'   THEN %s ELSE vuelto_at END, "
                "  datos = datos || %s::jsonb "
                "WHERE clave = %s",
                (estado, ahora, estado, ahora, estado, ahora,
                 json.dumps({"por": por} if por else {}), clave))
        return {"ok": True, "de": actual, "a": estado}
    except Exception as e:
        logger.warning("av_agent_items: no pude marcar %s (%s)", clave, e)
        return {"ok": False, "error": str(e)[:200]}


def abiertos(tipo: str = "", limite: int = 400) -> list[ciclo.Item]:
    """Lo que sigue vivo. **Incluye `volvio`**: un problema que reapareció está
    abierto, y además merece más atención que uno nuevo."""
    where = "estado NOT IN ('resuelto', 'ignorado')"
    params: list = []
    if tipo:
        where += " AND tipo = %s"
        params.append(tipo)
    params.append(max(1, min(int(limite), 2000)))
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(_COLS)} FROM mercado.av_agent_items "
                        f"WHERE {where} ORDER BY ultimo_at DESC LIMIT %s",
                        tuple(params))
            return [_fila(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("av_agent_items: no pude listar (%s)", e)
        return []


def en_seguimiento() -> list[dict]:
    """Los arreglos que se están mirando, **con cuántos hitos llevan**.

    El user: *«5 días es mucho, es el día siguiente para ver si vuelve. Pero a
    su vez tiene que tener memoria y recursos para que siga con el paso del
    tiempo: puede ser 2 días, 3 días…»*.

    Por eso no es un plazo: son HITOS (1·2·3·7·14·30). El primero da la señal
    rápida —si vuelve mañana, el arreglo no sirvió— y los siguientes acumulan
    confianza, que es lo que después habilita autonomía. **Volver una vez borra
    todo lo acumulado**: un arreglo que falla al día 8 no es «7 días bueno».
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_COLS)} FROM mercado.av_agent_items "
                "WHERE estado = 'resuelto' AND resuelto_at IS NOT NULL "
                "ORDER BY resuelto_at DESC LIMIT 500")
            items = [_fila(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("av_agent_items: no pude leer el seguimiento (%s)", e)
        return []
    out = []
    for it in items:
        d = it.dias_resuelto()
        out.append({
            "clave": it.clave, "sujeto": it.sujeto, "regla": it.regla,
            "titulo": it.titulo, "dias": round(d, 2),
            "hitos": ciclo.hitos_cumplidos(d), "de": len(ciclo.HITOS_DIAS),
            "confianza": ciclo.confianza(d),
            "proximo_hito_en_dias": ciclo.proximo_hito(d),
            "aguanto": ciclo.hitos_cumplidos(d) >= len(ciclo.HITOS_DIAS)})
    return out


def resumen() -> dict:
    """Cuántos hay de cada estado y de cada tipo. Una query."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT tipo, estado, count(*) "
                        "FROM mercado.av_agent_items GROUP BY tipo, estado")
            filas = cur.fetchall()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    por_estado: dict[str, int] = {}
    por_tipo: dict[str, int] = {}
    for tipo, estado, n in filas:
        por_estado[estado] = por_estado.get(estado, 0) + n
        por_tipo[tipo] = por_tipo.get(tipo, 0) + n
    return {"ok": True, "por_estado": por_estado, "por_tipo": por_tipo,
            "total": sum(por_estado.values())}
