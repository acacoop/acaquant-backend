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

# QUÉ TIPOS cubre cada pasada. Se declara —y no se deduce de lo que devolvió—
# porque **una pasada que no encontró nada y una que EXPLOTÓ devuelven lo
# mismo: nada**. Sin esta lista, la única forma de distinguirlas sería que el
# detector encontrara algo, que es justo lo que no se puede exigir.
# ⚠️⚠️ **ESTA LISTA TIENE DOS CONSUMIDORES Y HACÍA DOS COSAS DISTINTAS**
# (2026-08-24). Es el catálogo de QUÉ VIGILA el centinela, y lo leen:
#
#   · la tab AGENDA (`av_agent_agenda._del_daemon`) → «¿qué mira el daemon?»
#   · el auto-resuelto de `ciclo()`                 → «¿qué puedo dar por cerrado?»
#
# Y para la segunda pregunta **estaba mal por construcción**: la pasada de
# precios corre SEIS detectores y acá se nombraban tres. `motor_caido`,
# `motor_ruidoso`, `proveedor_caido` y `latencia` quedaban afuera, con dos
# efectos que nadie cruzó:
#
#   · **no se cerraban nunca** → un motor que volvía seguía en AHORA y en
#     VIGILANCIA para siempre, que es cómo una alarma deja de mirarse;
#   · y la AGENDA decía que el daemon **no vigila los motores** — la pantalla
#     que existe para contestar *«¿hay algo que NO estoy haciendo?»* contestaba
#     que no los mira, mientras los mira cada 30 segundos.
#
# Ahora la lista está COMPLETA (arregla la agenda) y **el auto-resuelto ya no la
# usa**: qué se alcanzó a mirar EN ESTA PASADA lo declara cada detector cuando
# termina bien (`relevar_live()["evaluados"]`). Son dos preguntas distintas —
# *qué vigilo* y *qué pude mirar recién*— y una sola lista no puede contestar
# las dos: si se la completa, un detector caído cierra todo lo suyo por
# ausencia; si se la recorta, la agenda miente.
_CUBRE = {
    # La pasada de precios de `relevar_live()`, COMPLETA. `recuperado` lo
    # produce el cron y no el daemon, pero el daemon puede cerrarlo.
    # `recuperado` y `respuesta` los produce `_escribir_la_foto` (lo que hacía el
    # cron antes de que el daemon lo absorbiera): van a la FOTO y no a esta
    # tabla, pero el daemon **sí los vigila** y la tab AGENDA lee de acá.
    "precios": ("sin_precio", "precio_moneda", "recuperado", "respuesta",
                "latencia", "motor_caido", "motor_ruidoso", "proveedor_caido"),
    "tasas": ("tasa_sospechosa",),
    "salud": ("salud",),
    # Solo se evalúa los días NO hábiles (es cuando el universo prohibido
    # existe): sus hallazgos se cierran recién el próximo día no hábil limpio,
    # no el lunes — que la actividad vuelva a ser legal no es que se arregló.
    "actividad": ("actividad",),
}


def _observar() -> tuple[list[dict], set[str]]:
    """UNA pasada. Devuelve lo que vio **y QUÉ ALCANZÓ A MIRAR**.

    Cada bloque en su propio `try`: que SALUD se caiga no puede dejar al
    centinela sin mirar los precios, que es lo que la mesa necesita en rueda.

    ⚠️⚠️ **Y POR ESO HAY QUE DEVOLVER LO SEGUNDO.** Los tres `try` se tragan la
    excepción y devuelven una lista más corta — con lo cual el que llama no
    puede distinguir *«no encontró nada»* de *«explotó»*. El auto-resuelto de
    abajo cerraba TODO lo que no apareciera, así que **una caída del bloque de
    tasas marcaba todos los `tasa_sospechosa` como "se arregló solo"**: en
    silencio, y del lado optimista.

    Es el mismo modo de falla que se tapó en el censo con `evaluados` (§0.be), y
    acá estaba igual de abierto. Ahora una pasada que falla **no cierra lo
    suyo**: se queda como estaba, que es lo honesto.
    """
    from api.services import av_agent

    hallazgos: list[dict] = []
    evaluados: set[str] = set()

    # ⚠️⚠️ **LOS DETECTORES DE MERCADO SOLO CORREN CON EL MERCADO ABIERTO**
    # (user, 2026-08-22, un sábado con 7 «volvió», 11 «apareció» y 35 «se
    # arregló»: *«hoy es SÁBADO, el mercado no abre — no puede pasar que un
    # día no hábil se rompa algo»*). Fuera de rueda el snapshot es una FOTO
    # VIEJA: cualquier cambio que el detector vea ahí es churn del detector,
    # no de la base — la misma lección de §0.u, ahora aplicada al espejo en
    # items. Al no correr, los tipos quedan FUERA de `evaluados`: no se cierra,
    # no se reabre y no nace nada desde una foto que no puede haber cambiado.
    # `en_rueda()` además ya sabe de feriados (mismo commit).
    if av_agent.en_rueda():
        try:
            r = av_agent.relevar_live()
            hallazgos.extend(r.get("hallazgos") or [])
            # ⚠️ **Lo que la pasada DECLARÓ haber mirado, detector por
            # detector** — NO `_CUBRE["precios"]`. Los seis detectores corren
            # cada uno en su `try`: usar el catálogo daría por evaluado lo que
            # explotó, y eso cierra por ausencia lo que sigue roto (§0.be). El
            # catálogo dice qué VIGILA; esto dice qué pudo MIRAR recién.
            evaluados.update(r.get("evaluados") or ())
        except Exception as e:
            logger.exception("centinela: la pasada de precios falló: %s", e)

        # TASAS sobre el snapshot LIVE. El detector es el mismo que corre de
        # noche sobre el cierre — reusarlo es lo que garantiza que el centinela
        # y la relevada nocturna no puedan discrepar sobre qué es una tasa
        # sospechosa.
        try:
            from core import curvas_sql, market_snapshot
            docs = curvas_sql.cargar_todos() or []
            simbolos = [(d.get("ticker") or "").strip()
                        for d in docs if d.get("ticker")]
            met = market_snapshot.cols_map(
                simbolos, ["tea", "paridad", "duration", "last_price"]) or {}
            hallazgos.extend(av_agent.detectar_tasas_sospechosas(docs, met))
            evaluados.update(_CUBRE["tasas"])
        except Exception as e:
            logger.exception("centinela: la pasada de tasas falló: %s", e)

    # En día NO hábil el razonamiento se INVIERTE: no se mira si el dato está
    # bien — se mira que no haya dato nuevo. Motores escribiendo un sábado =
    # algo quedó prendido o un cron corre cuando no debe (§0.co).
    if not av_agent.dia_habil():
        try:
            hallazgos.extend(av_agent.detectar_actividad_no_habil())
            evaluados.update(_CUBRE["actividad"])
        except Exception as e:
            logger.exception("centinela: la pasada de actividad falló: %s", e)

    try:
        from api.services import salud
        hallazgos.extend(av_agent.detectar_salud(salud.evaluar()))
        evaluados.update(_CUBRE["salud"])
    except Exception as e:
        logger.exception("centinela: la pasada de salud falló: %s", e)

    return hallazgos, evaluados


# ── LA FOTO DE LA RUEDA, escrita por acá (Fase 2, 2026-08-24) ──────────────
#
# ⚠️⚠️ **HABÍA DOS PROCESOS CORRIENDO EL MISMO DETECTOR.** El cron
# `jobs.av_agent_live` (cada 5 min) y este daemon (cada 30 s) llamaban los dos a
# `relevar_live()` —el barrido completo: master, snapshot, especies, latencia,
# motores, logs, proveedores— y escribían en tablas distintas: el cron la FOTO
# que lee LA LISTA, el daemon los OBJETOS que leen AHORA y VIGILANCIA.
#
# No era una transición: nunca se decidió. Dos procesos, el doble de trabajo, y
# dos cosas que pueden fallar por separado sobre el mismo dato.
#
# Ahora el daemon escribe las dos. Y la foto va **throttleada a la cadencia que
# tenía el cron**: el daemon late cada 30 s y reescribir la foto entera
# (DELETE + INSERT) ocho veces por minuto sería diez veces el churn de hoy para
# un dato que la pantalla mira cada tanto.
SEGUNDOS_ENTRE_FOTOS = 5 * 60
_ultima_foto: float = 0.0


def _toca_la_foto() -> bool:
    """¿Pasaron los 5 minutos? Arranca en `True` (la primera pasada la escribe).

    Es tiempo de RELOJ y no de mercado a propósito: la foto tiene que
    refrescarse igual fuera de rueda, porque ahí es cuando los hallazgos de
    mercado VENCEN y hay que dejar de mostrarlos.
    """
    return (time.monotonic() - _ultima_foto) >= SEGUNDOS_ENTRE_FOTOS


def _escribir_la_foto(hallazgos: list[dict]) -> int:
    """La foto `live` + lo que el cron hacía justo antes de pisarla.

    **El orden importa**: `detectar_recuperados` lee la foto ANTERIOR (todavía
    está en la tabla hasta que `reemplazar_hallazgos` haga su DELETE) y la resta
    contra la nueva. Es el único momento en que se puede saber qué se arregló;
    después, una recuperación es una fila que deja de escribirse, o sea silencio.

    Nunca levanta: la foto es importante pero los objetos son la memoria, y esta
    pasada corre después de que ya se escribieron.
    """
    global _ultima_foto
    from api.services import av_agent
    from api.services.av_agent_recuperados import detectar_recuperados
    from api.services.av_agent_respuesta import revisar

    extra: list[dict] = []
    try:
        extra += detectar_recuperados(hallazgos)
    except Exception as e:
        logger.warning("centinela: no pude detectar recuperados (%s)", e)
    try:
        # Lo que se le pidió al mercado y ya contestó. La espera se mide en
        # tiempo de RUEDA, así que solo tiene sentido con el mercado abierto.
        extra += revisar()
    except Exception as e:
        logger.warning("centinela: no pude revisar las respuestas (%s)", e)

    n = av_agent.reemplazar_hallazgos("live", list(hallazgos) + extra)
    _ultima_foto = time.monotonic()
    return n


def ciclo() -> dict:
    """Observa, concilia contra lo que ya estaba, y late. Nunca levanta."""
    t0 = time.perf_counter()
    from api.services import av_agent
    abierto = av_agent.en_rueda()
    err = ""
    nuevos = abiertos = fotos = 0
    try:
        hallazgos, evaluados = _observar()
        # Dedup por clave DENTRO de la pasada: dos detectores pueden ver el mismo
        # problema (un bono sin precio también sale sin TEA) y eso es una fila,
        # no dos.
        por_clave: dict[str, dict] = {}
        for h in hallazgos:
            por_clave.setdefault(_clave(h), h)

        from api.services import av_agent_items

        marca = datetime.now(UTC)
        with get_pool().connection() as conn, conn.cursor() as cur:
            for clave, h in por_clave.items():
                cur.execute(
                    "INSERT INTO agente.av_agent_centinela "
                    "(clave, tipo, sujeto, regla, severidad, motivo, evidencia, "
                    " clave_item) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s) "
                    "ON CONFLICT (clave) DO UPDATE SET "
                    # El motivo y la evidencia SÍ se refrescan: son el estado de
                    # AHORA. Lo que nunca se pisa es `abierto_at` ni `visto_at`.
                    "  motivo = EXCLUDED.motivo, evidencia = EXCLUDED.evidencia, "
                    "  severidad = EXCLUDED.severidad, ultimo_at = now(), "
                    "  veces = agente.av_agent_centinela.veces + 1, "
                    # Si volvió, REABRE la misma fila. Un problema que va y viene
                    # es UN problema intermitente, no cinco problemas distintos.
                    # Y se CUENTA la reapertura: «esto ya lo arreglamos tres
                    # veces y vuelve» es un dato distinto de «pasa hace tres
                    # días», y hasta hoy los dos se veían igual. Un problema que
                    # reaparece no es el de siempre — es uno que no entendimos.
                    "  reaperturas = agente.av_agent_centinela.reaperturas + "
                    "    CASE WHEN agente.av_agent_centinela.resuelto_at "
                    "         IS NOT NULL THEN 1 ELSE 0 END, "
                    "  resuelto_at = NULL, resuelto_como = NULL, "
                    # Se refresca por si la fila es vieja (nació antes de que
                    # existiera la columna) o si `causa_canonica` cambió.
                    "  clave_item = EXCLUDED.clave_item "
                    "RETURNING (xmax = 0) AS es_nuevo",
                    (clave, h.get("tipo"), h.get("ticker") or "?", h.get("regla"),
                     h.get("severidad"), h.get("motivo"),
                     json.dumps(h.get("evidencia") or {}, ensure_ascii=False,
                                default=str),
                     # LA MISMA función que usan el detector, el job y la
                     # acción. Nunca a mano: tres implementaciones de la
                     # identidad es cómo se llegó hasta acá (REGLA #9).
                     av_agent_items.clave_de_problema(
                         h.get("ticker") or "", h.get("regla") or "")))
                if (r := cur.fetchone()) and r[0]:
                    nuevos += 1

            # AUTO-RESUELTOS: los que no aparecieron en ESTA pasada.
            #
            # ⚠️⚠️ **SOLO DE LOS TIPOS QUE SE ALCANZARON A MIRAR.** Antes la
            # condición era `if hallazgos:` — o sea, «si algo trajo, cerrá todo
            # lo demás». El comentario decía «solo cuando la pasada fue
            # COMPLETA» y **eso no era lo que el código chequeaba**: los tres
            # bloques de `_observar` se tragan su excepción, así que una caída
            # del bloque de tasas dejaba pasar la condición igual (los precios
            # sí habían traído algo) y marcaba **todos los `tasa_sospechosa`
            # como "se arregló solo"**.
            #
            # Silenciosa y del lado optimista, que es la peor combinación. Ahora
            # `_observar` declara qué alcanzó a mirar y solo eso se cierra.
            if evaluados:
                cur.execute(
                    "UPDATE agente.av_agent_centinela SET resuelto_at = now(), "
                    "  resuelto_como = 'solo' "
                    "WHERE resuelto_at IS NULL AND ultimo_at < %s "
                    "  AND tipo = ANY(%s)", (marca, sorted(evaluados)))
            cur.execute("SELECT count(*) FROM agente.av_agent_centinela "
                        "WHERE resuelto_at IS NULL")
            abiertos = cur.fetchone()[0]
            conn.commit()

        # ── Y LA FOTO, que es lo que lee LA LISTA ───────────────────────────
        # Throttleada: el daemon late cada 30 s y la foto se reescribe entera.
        # Va DESPUÉS de la transacción de arriba y en su propio try.
        if _toca_la_foto():
            try:
                fotos = _escribir_la_foto(list(por_clave.values()))
            except Exception as e:
                logger.warning("centinela: no pude escribir la foto live (%s)", e)

        # ── Y COMO OBJETO, para que AHORA y ENCONTRÓ hablen de lo mismo ─────
        #
        # Las dos pantallas mostraban el mismo problema con historias
        # distintas: el centinela tenía su `veces` y su `abierto_at`, la
        # relevada nocturna tenía otros, y nadie los unía. Con la misma clave
        # es UN objeto — y la antigüedad que ves en ENCONTRÓ es la misma que ve
        # el monitor.
        #
        # Va FUERA de la transacción y en su propio `try`: la pasada del
        # centinela es lo que la mesa mira en rueda y no se cae por el espejo.
        try:
            from api.services import av_agent_items
            av_agent_items.sincronizar("live", list(por_clave.values()),
                                       evaluados=evaluados)
        except Exception as e:
            logger.warning("centinela: no pude espejar en items (%s)", e)
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
            "nuevos": nuevos, "ms": ms, "error": err, "foto": fotos}


def _latir(abierto: bool, abiertos: int, nuevos: int, ms: int, err: str,
           proximo_en_s: int) -> None:
    """El latido. **Se escribe también cuando el ciclo falló** — un centinela que
    solo late cuando todo sale bien se ve idéntico a uno muerto, y esa es
    justamente la diferencia que el círculo tiene que mostrar."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE agente.av_agent_latido SET at = now(), ciclo = ciclo + 1, "
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

# Los tipos que van a AHORA estén o no de hoy. Se declara UNA vez en
# `av_agent.EN_AHORA_SIEMPRE` y se lee de ahí: una segunda lista acá se separaría
# de la primera sin dar ningún error.
def _en_ahora() -> tuple[str, ...]:
    from api.services.av_agent import EN_AHORA_SIEMPRE
    return EN_AHORA_SIEMPRE


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
                        "FROM agente.av_agent_latido WHERE id")
            lat = cur.fetchone()
            # ⚠️ **LA ANTIGÜEDAD SALE DEL OBJETO, NO DE ESTA TABLA** (§0.bj).
            #
            # El centinela tenía su `abierto_at` y el censo el suyo, y nadie los
            # unía: **AHORA podía decir «recién» y ENCONTRÓ «11 días» del MISMO
            # problema**. Dos relojes para un hecho es la definición de la
            # contradicción que esta migración vino a terminar.
            #
            # El JOIN va por la clave canónica —(sujeto, causa)— y en la MISMA
            # query: el peaje de Supabase se paga por viaje.
            cur.execute(
                # ⚠️ `vuelto_at` viene del OBJETO y no de esta tabla: el
                # centinela no lo tiene (tiene `reaperturas`, un contador sin
                # fecha, que no sirve para decir si volvió HOY). Sale del mismo
                # JOIN — un viaje más a Supabase por un dato que ya viaja.
                f"SELECT {', '.join('c.' + x for x in _COLS)}, i.abierto_at, "
                "       i.vuelto_at "
                "  FROM agente.av_agent_centinela c "
                # ⚠️ **Por la clave GUARDADA, no recalculada.** Acá vivía
                # `ON i.clave = lower(c.sujeto) || '|' || lower(c.regla)`: una
                # TERCERA implementación de `clave_de_problema`, en SQL, sin
                # `causa_canonica()` (los sinónimos control↔detector no
                # matcheaban) y sin el caso del sujeto vacío. Fallaba en
                # silencio: AHORA decía «recién» y ENCONTRÓ «11 días» del mismo
                # problema, que es justo lo que este JOIN vino a arreglar.
                "  LEFT JOIN agente.av_agent_items i ON i.clave = c.clave_item "
                " WHERE c.resuelto_at IS NULL "
                # Lo NUEVO y sin ver primero: es lo único que pide una decisión.
                " ORDER BY (c.visto_at IS NULL) DESC, "
                "  CASE c.severidad WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, "
                "  c.abierto_at DESC LIMIT %s", (limite,))
            abiertos = [dict(zip(_COLS + ["abierto_canonico", "vuelto_at"],
                                 r, strict=True))
                        for r in cur.fetchall()]
            # Lo que se arregló SOLO en las últimas horas. Sirve para dos cosas:
            # confirmar que algo que estabas por atender ya no está, y ver los
            # intermitentes (los que se resuelven y vuelven).
            cur.execute(
                f"SELECT {', '.join(_COLS)} FROM agente.av_agent_centinela "
                "WHERE resuelto_at > now() - interval '8 hours' "
                "ORDER BY resuelto_at DESC LIMIT 40")
            resueltos = [dict(zip(_COLS, r, strict=True)) for r in cur.fetchall()]

            # ⚠️ **LOS MOTORES NO VIVEN EN ESTA TABLA** y por eso AHORA nunca los
            # mostró. `agente.av_agent_centinela` guarda lo que el DAEMON mira
            # (precios · tasas · salud, ver `_CUBRE`); los motores los encuentra
            # el cron `jobs.av_agent_live` y quedan en `agente.av_agent_items`.
            # Dos tablas para dos productores del mismo objeto, y la pantalla
            # leía una sola.
            #
            # Se traen acá —un viaje más, en la MISMA conexión— y no con otra
            # llamada desde el front: el peaje de Supabase se paga por viaje, y
            # dos requests podrían mostrar dos fotos distintas del mismo momento.
            cur.execute(
                "SELECT clave, tipo, sujeto, regla, severidad, titulo, veces, "
                "       abierto_at, ultimo_at, visto_at, datos "
                "  FROM agente.av_agent_items "
                " WHERE estado NOT IN ('resuelto', 'ignorado') "
                "   AND tipo = ANY(%s) "
                " ORDER BY ultimo_at DESC LIMIT 40",
                (list(_en_ahora()),))
            rotos_items = cur.fetchall()
    except Exception as e:
        return {**fuera, "error": str(e)}

    # A la MISMA forma que el resto: la pantalla dibuja una fila, no dos.
    #
    # ⚠️ **SIN DUPLICAR EL MISMO PROBLEMA** (2026-08-22): el error de un motor
    # puede estar en LAS DOS tablas (el daemon lo vio vía salud y el cron lo
    # escribió como item) con claves de formato distinto — así «ROTO AHORA»
    # mostró 8 filas que eran 4, cada una dos veces. La identidad del problema
    # es (sujeto, causa), no la clave de cada tabla: si ya está, no se anexa.
    ya = {((f.get("sujeto") or "").strip().lower(),
           (f.get("regla") or "").strip().lower()) for f in abiertos}
    for (clave, tipo, suj, regla, sev, titulo, veces, ab, ult, vis,
         datos) in rotos_items:
        if ((suj or "").strip().lower(), (regla or "").strip().lower()) in ya:
            continue
        d = datos or {}
        abiertos.append({
            "id": None, "clave": clave, "tipo": tipo, "sujeto": suj,
            "regla": regla, "severidad": sev,
            # El motivo LARGO si el hallazgo lo trae. La evidencia de un motor
            # ya viene con QUÉ PASÓ · A QUÉ AFECTA · SI SIGUE (`evidencia.texto`)
            # y la pantalla mostraba solo el título recortado — el user: *«sin
            # información, sin contexto… si tenemos los logs tenemos los datos»*.
            # Los datos estaban; no se dibujaban.
            "motivo": titulo, "detalle": str(d.get("texto") or ""),
            "muestra": str(d.get("muestra") or "")[:400],
            # La VENTANA que la pieza declaró (`rueda`, `12-23 UTC`, …): es lo
            # que deja decidir si esto puede estar roto HOY (§0.cp).
            "ventana": d.get("ventana"),
            "veces": veces, "abierto_at": ab, "ultimo_at": ult,
            "visto_at": vis, "resuelto_at": None, "resuelto_como": None,
            "abierto_canonico": ab, "vuelto_at": None,
        })

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
        # La canónica gana: es la que ve ENCONTRÓ. Si el objeto todavía no
        # existe (un hallazgo de este mismo ciclo, antes de espejarse) se usa la
        # local — es lo mismo en ese instante y evita un hueco en la pantalla.
        desde = f.pop("abierto_canonico", None) or f.get("abierto_at")
        # La ventana declarada también para las filas de la tabla propia (las
        # de items la traen puesta): sin esto el filtro de no-hábil no puede
        # distinguir la pieza de rueda de la que corre todos los días.
        if "ventana" not in f:
            ev = f.get("evidencia")
            f["ventana"] = (ev or {}).get("ventana") if isinstance(ev, dict) else None
        f["recien"] = bool(desde and (ahora - desde).total_seconds() < RECIEN_S)
        # Y se publica, para que la fila pueda decir «11d» igual que ENCONTRÓ.
        if desde:
            f["dias_abierto"] = round((ahora - desde).total_seconds() / 86400, 1)
        for k in ("abierto_at", "ultimo_at", "visto_at", "resuelto_at",
                  "vuelto_at"):
            if k in f:
                f[k] = f[k].isoformat() if f[k] else None
        # El mismo eje que la vista (`av_agent.de_quien`), derivado de la MISMA
        # tabla declarada: si el centinela tuviera su propia idea de qué es
        # iliquidez, AHORA y ENCONTRÓ se contradirían sobre el mismo hallazgo.
        f["de_quien"] = av_agent.de_quien(f.get("regla") or "")
        # ⚠️ **EL NOMBRE LEGIBLE, igual que en ENCONTRÓ.** El user, viendo
        # `control:patas_equiv…` cortado en AHORA: *«los títulos no pueden estar
        # así cortados, no se entiende nada»*. Y el nombre humano ya existía —
        # ENCONTRÓ lo publica desde §0.bq y esta pantalla no.
        #
        # Se deriva ACÁ y no en el front por la razón de siempre: dos pantallas
        # que muestran el mismo hallazgo tienen que llamarlo igual, y un segundo
        # criterio del lado del navegador se separa del primero sin dar error.
        f["nombre"] = _nombre_legible(f.get("sujeto") or "", f.get("tipo") or "")

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
            # ¿HOY es día hábil? Define el UNIVERSO del día: en no hábil los
            # motores están apagados a propósito, nada de rueda se re-evalúa,
            # y lo único que NO puede pasar es actividad de mercado. La
            # pantalla lo dice con esto — sin el dato, un sábado tranquilo y
            # un lunes roto se dibujan igual.
            "habil": av_agent.dia_habil(),
            "abiertos": abiertos, "resueltos": resueltos,
            "sin_ver": sum(1 for f in abiertos if not f["visto_at"]),
            # ── LO DE HOY, SEPARADO DEL ARRASTRE (§0.bo) ────────────────────
            "hoy": _lo_de_hoy(abiertos, resueltos,
                              habil=av_agent.dia_habil())}


# ⚠️⚠️ **AHORA ES EL DÍA DE HOY, NO EL ACUMULADO.** El user, 2026-08-22:
# *«AHORA es para lo que está pasando EXCLUSIVAMENTE en el día de hoy…
# ENCONTRÓ es donde está toda la cocina para solucionar cosas»*.
#
# La tab decía **AHORA 1** y abajo mostraba un control abierto hacía 21 horas,
# con 131 plegados y 40 resueltos. O sea: dos backlogs con nombres distintos,
# que es exactamente por qué nadie podía decir en qué se diferencian.
#
# El corte va acá y no en la pantalla por lo de siempre: el navegador no puede
# mirar el reloj mientras dibuja, y el criterio tiene que ser UNO.
_ART = "America/Argentina/Buenos_Aires"


def _arranco_el_dia() -> datetime:
    """Medianoche de HOY en hora argentina, en UTC.

    ⚠️ **En ART y no en UTC.** El día UTC arranca a las 21:00 de acá: con el
    corte en UTC, algo que pasó a las 21:30 de anoche saldría como «de hoy» y
    lo de esta mañana temprano también — dos días distintos mezclados bajo el
    mismo rótulo, justo en la tab que no puede fallar.
    """
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(_ART)
    hoy = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return hoy.astimezone(UTC)


def _hoy(iso: str | None, desde: datetime) -> bool:
    if not iso:
        return False
    try:
        return datetime.fromisoformat(iso) >= desde
    except ValueError:
        return False


def _nombre_legible(sujeto: str, tipo: str) -> str:
    """`control:patas_equivocadas` → `patas equivocadas`.

    Los sujetos de SALUD llevan el prefijo de su familia (`control:` · `job:`)
    porque **la clave lo necesita**: un job y un control pueden llamarse igual y
    son cosas distintas. Pero en la pantalla ese prefijo se come el ancho de la
    columna y deja el nombre cortado — que es justo lo que el user no puede leer.
    La familia YA se ve en la columna de al lado («SALUD CONTROL»), así que acá
    es redundante.

    El sujeto crudo sigue viajando: la fila lo pone en el `title`, porque para
    buscarlo en la base hace falta el nombre exacto.
    """
    s = (sujeto or "").strip()
    for pref in ("control:", "job:", "tabla:", "motor:"):
        if s.lower().startswith(pref):
            s = s[len(pref):]
            break
    # Los tickers NO se tocan: `AL30` en minúsculas se lee peor, no mejor.
    return s.replace("_", " ") if ("_" in s or " " in s) else s


def _por_hora(filas: list[dict]) -> list[dict]:
    """De lo MÁS RECIENTE a lo más viejo, por la MISMA fecha que muestra la fila.

    ⚠️ El user: *«no está ordenado por hora, fijate el horario»* — y era cierto:
    la lista salía en el orden en que la devolvía la query (por severidad y
    clave), así que se leían 05:10 p.m. · 12:32 · 12:32 · 12:32 · 01:30 p.m.
    Ordenar en el backend y no en el front es lo que garantiza que el orden y el
    horario impreso salgan del MISMO campo: dos criterios para lo mismo es cómo
    nacieron las contradicciones que este agente ya se comió tres veces.
    """
    def _cuando(f: dict) -> str:
        # El mismo `coalesce` que usa la columna de la hora en la pantalla.
        return str(f.get("vuelto_at") or f.get("resuelto_at")
                   or f.get("abierto_at") or f.get("ultimo_at") or "")
    return sorted(filas, key=_cuando, reverse=True)


def _puede_pasar_hoy(f: dict, habil: bool) -> bool:
    """¿Esta fila puede ser una NOVEDAD hoy? (§0.cp)

    En día hábil, todo. En día NO hábil el universo se achica, y el user lo
    dijo con todas las letras un sábado con la pantalla llena: *«AHORA es
    AHORA — son problemas que tienen que estar PASANDO ahora mismo»*.

      · lo de dominio BONO no puede ser novedad: ningún detector de mercado
        corre (una marca de hoy es residuo de antes del gating, no un hecho)
      · un motor de RUEDA no puede estar «roto ahora»: está APAGADO a
        propósito (§0.r: fuera de rueda no está caído, está apagado). La
        pieza que declara ventana propia (Finnhub corre todos los días) SÍ
        puede — la ventana la declara la pieza, no la adivina esta función
      · los logs de motores (`motor_ruidoso`) son de procesos de rueda: hoy
        no producen líneas nuevas
      · `actividad`, `proveedor_caido` y SALUD pueden romperse cualquier día
    """
    if habil:
        return True
    from api.services import av_agent
    t = (f.get("tipo") or "").strip()
    if t == "motor_ruidoso":
        return False
    if t == "motor_caido":
        return (f.get("ventana") or "rueda") not in ("rueda", "rueda_agro")
    return av_agent.dominio_eval(t) != "bono"


def _lo_de_hoy(abiertos: list[dict], resueltos: list[dict],
               habil: bool = True) -> dict:
    """Las TRES novedades del día. Nada más, y por eso sirve.

        apareció   algo que no estaba ayer
        volvió     un arreglo que falló — lo que más informa de todo
        se arregló cerró hoy, solo

    Lo que sigue abierto de antes **no es una novedad**: es trabajo pendiente y
    vive en ENCONTRÓ, con sus botones. Meterlo acá es lo que convertía a AHORA
    en un segundo depósito.
    """
    from api.services.av_agent import va_en_ahora

    desde = _arranco_el_dia()

    # ⚠️ **LO QUE ESTÁ ROTO AHORA, sea o no novedad** (§0.bz). El user:
    # *«¿que estas alertas no estén en el AHORA?? ¿Cómo no me va a avisar justo
    # de los motores en el AHORA?»*. Tenía razón: con el corte por día, un motor
    # roto desde hace tres días NO entraba — **cuanto más tiempo llevaba roto,
    # menos visible era**. La novedad sirve para un hallazgo de catálogo, que
    # espera; es el peor criterio para la infraestructura que está corriendo.
    #
    # Sale PRIMERO y se excluye de los otros tres bloques: la misma fila en dos
    # lugares de la misma pantalla se lee como dos problemas.
    #
    # ⚠️⚠️ Y **solo lo que puede estar roto HOY** (§0.cp): un deadlock del
    # viernes al mediodía no es «roto ahora» un sábado con el motor apagado —
    # es trabajo pendiente, y vive en ENCONTRÓ hasta el próximo hábil.
    roto = [f for f in abiertos
            if va_en_ahora(f.get("tipo") or "") and _puede_pasar_hoy(f, habil)]
    claves_roto = {f.get("clave") for f in roto}

    aparecio = [f for f in abiertos
                if _hoy(f.get("abierto_at"), desde) and not f.get("vuelto_at")
                and f.get("clave") not in claves_roto
                and _puede_pasar_hoy(f, habil)]
    volvio = [f for f in abiertos if _hoy(f.get("vuelto_at"), desde)
              and f.get("clave") not in claves_roto
              and _puede_pasar_hoy(f, habil)]
    cerro = [f for f in resueltos if _hoy(f.get("resuelto_at"), desde)
             and _puede_pasar_hoy(f, habil)]
    return {
        "desde": desde.isoformat(),
        "roto": _por_hora(roto),
        "aparecio": _por_hora(aparecio), "volvio": _por_hora(volvio),
        "se_arreglo": _por_hora(cerro),
        # El total que decide si la tab dice «hoy no pasó nada» o muestra algo.
        # `se_arreglo` NO suma: es una buena noticia, no una novedad que pida
        # atención — contarla haría subir el número cuando algo MEJORA.
        # `roto` SÍ suma: si hay un motor caído, «hoy no pasó nada» es mentira.
        "novedades": len(aparecio) + len(volvio) + len(roto),
    }


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
            cur.execute("UPDATE agente.av_agent_centinela SET visto_at = now(), "
                        "visto_por = %s WHERE clave = ANY(%s) AND visto_at IS NULL",
                        (por or None, claves))
            n = cur.rowcount
            conn.commit()
        return {"ok": True, "marcados": n}
    except Exception as e:
        return {"ok": False, "error": str(e)}
