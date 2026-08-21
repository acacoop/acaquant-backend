"""api/services/av_agent_centinela.py — EL AGENTE PRENDIDO, con memoria.

Doc madre: **`docs/AV_AGENT.md`** §0.k.

Pedido del user (2026-08-18): *«me encantaría verlo con un círculo verde de que
está prendido y vaya monitoreando en real time todo lo que vaya pasando… que no
haga nada de solucionar pero que sí me dé las cosas. El job actual es muy poco:
tiene que estar vigilando constantemente **sin pisar lo que ya reportó y todavía
no hice nada**»*.

**Esa última frase es la que define todo el diseño**, y es la diferencia entre un
monitor y un centinela.

El monitor anterior hacía `DELETE` + `INSERT` en cada pasada. Con eso es
imposible contestar tres preguntas que son las únicas que importan cuando algo
está mal:

    ¿esto es NUEVO?          → no se sabe: todo parece nuevo cada 5 minutos
    ¿desde CUÁNDO pasa?      → no se sabe: la fila nació recién
    ¿YA lo miré?             → no se sabe: la que miraste ya no existe

Acá cada hallazgo tiene **identidad estable** (`clave`) y un **ciclo de vida**.
Un problema que vuelve REABRE su fila; no nace otro. Uno que deja de verse se
marca resuelto — **nunca se borra**: «se arregló solo» es información, y borrarlo
sería la misma amnesia.

**Qué vigila** (todo local, cero créditos de 1816):

  · precios — no suscripto · sin punta · precio viejo (esto último SOLO en rueda)
  · tasas   — las reglas de `SALUD_CURVAS` sobre el snapshot LIVE: TEA fuera de
              rango, paridad imposible, `moneda_flujo` que contradice a los ejes
  · salud   — los chequeos del sistema que no están en verde (jobs, frescura)

**No arregla nada.** Es explícito: el user pidió un centinela, no un piloto
automático. Escribe en su propia tabla y en ninguna otra.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Cada cuánto late. 30s adentro de la rueda es "tiempo real" para un dato que el
# motor refresca cada pocos segundos, y son ~2 queries por ciclo: nada al lado
# del peaje que ya paga cualquier vista.
INTERVALO_RUEDA_S = 30
# Fuera de rueda sigue vivo pero mira poco: los precios no cambian y lo único que
# tiene sentido seguir es SALUD (un cron de la noche que falla).
INTERVALO_CERRADO_S = 300

# Cuántos ciclos perdidos hacen falta para declararlo muerto. Uno puede ser una
# query lenta; tres seguidos es que no está.
#
# ⚠️ **Es un MULTIPLICADOR, no un número de segundos** (fix 2026-08-18). Antes era
# `INTERVALO_RUEDA_S * 3` = 90s fijos, y fuera de rueda —donde late cada 5
# minutos— el círculo salía GRIS con el proceso perfectamente vivo: el umbral
# medía un ritmo y el daemon corría a otro. Ahora **el latido declara su propia
# cadencia** (`proximo_en_s`) y la tolerancia se deriva de ella, así cambiar un
# intervalo no puede volver a desincronizar el semáforo.
CICLOS_PERDIDOS = 3


def _clave(h: dict) -> str:
    """La IDENTIDAD de un hallazgo, estable entre ciclos.

    `tipo:sujeto:regla` y **no incluye el motivo**: el motivo lleva números que
    cambian en cada pasada («no se actualiza hace 12 min» → «hace 13 min»). Si
    entrara en la clave, cada ciclo crearía una fila nueva y volveríamos al
    problema que este módulo vino a resolver.
    """
    return f"{h.get('tipo')}:{h.get('ticker') or h.get('sujeto')}:{h.get('regla')}"


# ── El ciclo ────────────────────────────────────────────────────────────────

def _observar() -> list[dict]:
    """UNA pasada de observación. Función de LECTURA: no escribe nada.

    Cada bloque en su propio `try`: que SALUD se caiga no puede dejar al
    centinela sin mirar los precios, que es lo que la mesa necesita en rueda.
    """
    from api.services import av_agent

    hallazgos: list[dict] = []
    try:
        r = av_agent.relevar_live()
        hallazgos.extend(r.get("hallazgos") or [])
    except Exception as e:
        logger.exception("centinela: la pasada de precios falló: %s", e)

    # TASAS sobre el snapshot LIVE. El detector es el mismo que corre de noche
    # sobre el cierre — reusarlo es lo que garantiza que el centinela y la
    # relevada nocturna no puedan discrepar sobre qué es una tasa sospechosa.
    try:
        from core import curvas_sql, market_snapshot
        docs = curvas_sql.cargar_todos() or []
        simbolos = [(d.get("ticker") or "").strip() for d in docs if d.get("ticker")]
        met = market_snapshot.cols_map(
            simbolos, ["tea", "paridad", "duration", "last_price"]) or {}
        hallazgos.extend(av_agent.detectar_tasas_sospechosas(docs, met))
    except Exception as e:
        logger.exception("centinela: la pasada de tasas falló: %s", e)

    try:
        from api.services import salud
        hallazgos.extend(av_agent.detectar_salud(salud.evaluar()))
    except Exception as e:
        logger.exception("centinela: la pasada de salud falló: %s", e)

    return hallazgos


def ciclo() -> dict:
    """Observa, concilia contra lo que ya estaba, y late. Nunca levanta."""
    t0 = time.perf_counter()
    from api.services import av_agent
    abierto = av_agent.en_rueda()
    err = ""
    nuevos = abiertos = 0
    try:
        hallazgos = _observar()
        # Dedup por clave DENTRO de la pasada: dos detectores pueden ver el mismo
        # problema (un bono sin precio también sale sin TEA) y eso es una fila,
        # no dos.
        por_clave: dict[str, dict] = {}
        for h in hallazgos:
            por_clave.setdefault(_clave(h), h)

        marca = datetime.now(UTC)
        with get_pool().connection() as conn, conn.cursor() as cur:
            for clave, h in por_clave.items():
                cur.execute(
                    "INSERT INTO mercado.av_agent_centinela "
                    "(clave, tipo, sujeto, regla, severidad, motivo, evidencia) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb) "
                    "ON CONFLICT (clave) DO UPDATE SET "
                    # El motivo y la evidencia SÍ se refrescan: son el estado de
                    # AHORA. Lo que nunca se pisa es `abierto_at` ni `visto_at`.
                    "  motivo = EXCLUDED.motivo, evidencia = EXCLUDED.evidencia, "
                    "  severidad = EXCLUDED.severidad, ultimo_at = now(), "
                    "  veces = mercado.av_agent_centinela.veces + 1, "
                    # Si volvió, REABRE la misma fila. Un problema que va y viene
                    # es UN problema intermitente, no cinco problemas distintos.
                    # Y se CUENTA la reapertura: «esto ya lo arreglamos tres
                    # veces y vuelve» es un dato distinto de «pasa hace tres
                    # días», y hasta hoy los dos se veían igual. Un problema que
                    # reaparece no es el de siempre — es uno que no entendimos.
                    "  reaperturas = mercado.av_agent_centinela.reaperturas + "
                    "    CASE WHEN mercado.av_agent_centinela.resuelto_at "
                    "         IS NOT NULL THEN 1 ELSE 0 END, "
                    "  resuelto_at = NULL, resuelto_como = NULL "
                    "RETURNING (xmax = 0) AS es_nuevo",
                    (clave, h.get("tipo"), h.get("ticker") or "?", h.get("regla"),
                     h.get("severidad"), h.get("motivo"),
                     json.dumps(h.get("evidencia") or {}, ensure_ascii=False,
                                default=str)))
                if (r := cur.fetchone()) and r[0]:
                    nuevos += 1

            # AUTO-RESUELTOS: los que no aparecieron en ESTA pasada.
            # ⚠️ Solo cuando la pasada fue COMPLETA. Si un detector explotó, los
            # suyos faltan por el error y no porque se hayan arreglado — darlos
            # por resueltos sería el peor tipo de mentira: silenciosa y optimista.
            if hallazgos:
                cur.execute(
                    "UPDATE mercado.av_agent_centinela SET resuelto_at = now(), "
                    "  resuelto_como = 'solo' "
                    "WHERE resuelto_at IS NULL AND ultimo_at < %s", (marca,))
            cur.execute("SELECT count(*) FROM mercado.av_agent_centinela "
                        "WHERE resuelto_at IS NULL")
            abiertos = cur.fetchone()[0]
            conn.commit()
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.exception("centinela: el ciclo falló")

    ms = int((time.perf_counter() - t0) * 1000)
    # El ciclo dice cuándo piensa volver. Es LA MISMA cuenta que hace el daemon
    # para dormir — que salga de un solo lado es lo que evita que el semáforo y
    # el reloj discrepen.
    proximo = INTERVALO_RUEDA_S if abierto else INTERVALO_CERRADO_S
    _latir(abierto, abiertos, nuevos, ms, err, proximo)
    return {"ok": not err, "en_rueda": abierto, "abiertos": abiertos,
            "nuevos": nuevos, "ms": ms, "error": err}


def _latir(abierto: bool, abiertos: int, nuevos: int, ms: int, err: str,
           proximo_en_s: int) -> None:
    """El latido. **Se escribe también cuando el ciclo falló** — un centinela que
    solo late cuando todo sale bien se ve idéntico a uno muerto, y esa es
    justamente la diferencia que el círculo tiene que mostrar."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE mercado.av_agent_latido SET at = now(), ciclo = ciclo + 1, "
                "  en_rueda = %s, abiertos = %s, nuevos = %s, duracion_ms = %s, "
                "  error = %s, proximo_en_s = %s WHERE id",
                (abierto, abiertos, nuevos, ms, err or None, proximo_en_s))
            conn.commit()
    except Exception as e:
        logger.warning("centinela: no se pudo latir: %s", e)


# ── Lo que lee la pantalla ──────────────────────────────────────────────────

# Cuánto dura la palabra «nuevo». Dos horas: más que un par de corridas del
# monitor (5 min) y menos que una jornada — lo de la mañana no puede seguir
# anunciándose como novedad a la tarde.
RECIEN_S = 2 * 60 * 60

_COLS = ["id", "clave", "tipo", "sujeto", "regla", "severidad", "motivo",
         "evidencia", "abierto_at", "ultimo_at", "veces", "visto_at",
         "resuelto_at", "resuelto_como", "reaperturas"]


def estado(limite: int = 200) -> dict:
    """El tablero del centinela: el latido + lo abierto + lo que se arregló solo.

    UN request: la pantalla no puede pedir tres cosas para dibujar un círculo.
    """
    fuera = {"ok": False, "vivo": False, "abiertos": [], "resueltos": [],
             "latido": None}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT at, ciclo, en_rueda, abiertos, nuevos, "
                        "       duracion_ms, error, proximo_en_s "
                        "FROM mercado.av_agent_latido WHERE id")
            lat = cur.fetchone()
            cur.execute(
                f"SELECT {', '.join(_COLS)} FROM mercado.av_agent_centinela "
                "WHERE resuelto_at IS NULL "
                # Lo NUEVO y sin ver primero: es lo único que pide una decisión.
                "ORDER BY (visto_at IS NULL) DESC, "
                "  CASE severidad WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, "
                "  abierto_at DESC LIMIT %s", (limite,))
            abiertos = [dict(zip(_COLS, r, strict=True)) for r in cur.fetchall()]
            # Lo que se arregló SOLO en las últimas horas. Sirve para dos cosas:
            # confirmar que algo que estabas por atender ya no está, y ver los
            # intermitentes (los que se resuelven y vuelven).
            cur.execute(
                f"SELECT {', '.join(_COLS)} FROM mercado.av_agent_centinela "
                "WHERE resuelto_at > now() - interval '8 hours' "
                "ORDER BY resuelto_at DESC LIMIT 40")
            resueltos = [dict(zip(_COLS, r, strict=True)) for r in cur.fetchall()]
    except Exception as e:
        return {**fuera, "error": str(e)}

    from api.services import av_agent
    ahora = datetime.now(UTC)
    for f in abiertos + resueltos:
        # ⚠️ **«NUEVO» NO SE LE PUEDE DECIR A ALGO DE HACE 10 HORAS.** El user,
        # viendo un control bajo el título NUEVO, SIN VER con «desde hace 10 h ·
        # ×474» al lado: *«no termino de entender por qué muestra esto ahora»*.
        # Y no pasó nada ahora: lo único «nuevo» era que no había apretado el
        # botón de visto. **Sin ver y RECIÉN APARECIDO son dos cosas distintas**,
        # y llamarlas igual quema el rótulo: si lo que dice NUEVO tiene medio
        # día, ninguno de los otros carteles se lee en serio tampoco.
        #
        # Se calcula acá y no en la pantalla porque el navegador no puede mirar
        # el reloj mientras dibuja (y porque el criterio es uno solo, igual que
        # `de_quien`).
        f["recien"] = bool(
            f.get("abierto_at")
            and (ahora - f["abierto_at"]).total_seconds() < RECIEN_S)
        for k in ("abierto_at", "ultimo_at", "visto_at", "resuelto_at"):
            f[k] = f[k].isoformat() if f[k] else None
        # El mismo eje que la vista (`av_agent.de_quien`), derivado de la MISMA
        # tabla declarada: si el centinela tuviera su propia idea de qué es
        # iliquidez, AHORA y ENCONTRÓ se contradirían sobre el mismo hallazgo.
        f["de_quien"] = av_agent.de_quien(f.get("regla") or "")

    latido = None
    vivo = False
    if lat:
        edad = (datetime.now(UTC) - lat[0]).total_seconds()
        # **VIVO es una afirmación sobre AHORA**, no sobre la última vez que
        # corrió. Sin esta resta, el círculo quedaría verde para siempre después
        # de que el proceso muera — que es exactamente lo que no puede pasar.
        #
        # La tolerancia sale del RITMO QUE EL PROPIO LATIDO DECLARÓ, no de una
        # constante: en rueda late cada 30s y fuera cada 300, y un umbral fijo
        # daba por muerto a un proceso sano todas las noches.
        cadencia = lat[7] or INTERVALO_RUEDA_S
        vivo = edad < cadencia * CICLOS_PERDIDOS
        latido = {"at": lat[0].isoformat(), "hace_s": int(edad), "ciclo": lat[1],
                  "en_rueda": lat[2], "abiertos": lat[3], "nuevos": lat[4],
                  "duracion_ms": lat[5], "error": lat[6],
                  "cadencia_s": cadencia,
                  # Cuánto falta para que el círculo se apague si no vuelve a
                  # latir. Que el número esté a la vista es lo que hace que
                  # «apagado» se pueda verificar en vez de creerse.
                  "muere_en_s": max(0, int(cadencia * CICLOS_PERDIDOS - edad))}

    return {"ok": True, "vivo": vivo, "latido": latido,
            "abiertos": abiertos, "resueltos": resueltos,
            "sin_ver": sum(1 for f in abiertos if not f["visto_at"])}


def marcar_visto(claves: list[str], por: str = "") -> dict:
    """«Ya lo miré». **No lo resuelve ni lo esconde** — lo saca de «nuevo».

    Son dos cosas distintas y mezclarlas es lo que hace que la gente deje de
    tocar el botón: si marcar visto ocultara el hallazgo, nadie lo marcaría por
    miedo a perderlo de vista.
    """
    claves = [c for c in (claves or []) if c][:500]
    if not claves:
        return {"ok": False, "error": "no hay nada que marcar"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE mercado.av_agent_centinela SET visto_at = now(), "
                        "visto_por = %s WHERE clave = ANY(%s) AND visto_at IS NULL",
                        (por or None, claves))
            n = cur.rowcount
            conn.commit()
        return {"ok": True, "marcados": n}
    except Exception as e:
        return {"ok": False, "error": str(e)}
