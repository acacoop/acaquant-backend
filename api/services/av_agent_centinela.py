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


def _escribir_la_foto(hallazgos: list[dict], evaluados: set[str]) -> dict:
    """La foto `live` + lo que el cron hacía justo antes de pisarla.

    **El orden importa**: `detectar_recuperados` lee la foto ANTERIOR (todavía
    está en la tabla hasta que `reemplazar_hallazgos` haga su DELETE) y la resta
    contra la nueva. Es el único momento en que se puede saber qué se arregló;
    después, una recuperación es una fila que deja de escribirse, o sea silencio.

    Nunca levanta: la foto es importante pero los objetos son la memoria, y esta
    pasada corre después de que ya se escribieron.
    """
    global _ultima_foto
    from api.services import av_agent_registro as registro
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

    # ⚠️ **`objetos=hallazgos`, sin el extra.** `recuperado` y `respuesta` son
    # avisos TRANSITORIOS (vencen en 30 y 15 minutos): viajan en la foto porque
    # es lo que hay que mostrar ahora, pero darles ciclo de vida llenaría la
    # memoria de objetos que nacen y se cierran cada cinco minutos sin que nadie
    # los mire. Un problema tiene historia; una buena noticia no.
    r = registro.guardar("live", list(hallazgos) + extra,
                           evaluados=evaluados, objetos=list(hallazgos))
    _ultima_foto = time.monotonic()
    esp = r.get("espejo") or {}
    return {"foto": int(r.get("foto") or 0), "nuevos": int(esp.get("nuevos") or 0)}


def _contar_abiertos() -> int:
    """Cuántos problemas del daemon siguen abiertos. UNA query.

    Va contra `av_agent_items` —la tabla ÚNICA desde la Fase 3— y se cuenta en
    CADA latido, aunque esa vuelta no haya observado: el número del semáforo
    tiene que ser de ahora, no del último censo.
    """
    from api.services.av_agent import EN_AHORA_SIEMPRE

    tipos = sorted({t for ts in _CUBRE.values() for t in ts}
                   | set(EN_AHORA_SIEMPRE))
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agente.av_agent_items "
                    "WHERE estado NOT IN ('resuelto', 'ignorado') "
                    "  AND tipo = ANY(%s)", (tipos,))
        return int((cur.fetchone() or [0])[0])


def ciclo() -> dict:
    """Observa, escribe por la puerta, y late. Nunca levanta.

    ⚠️⚠️ **FASE 3 — YA NO HAY TABLA PROPIA** (2026-08-24). Hasta hoy esta
    función hacía un `UPSERT` en `agente.av_agent_centinela` con su propio ciclo
    de vida, su propio `abierto_at` y su propio `resuelto_como`, **en paralelo
    al objeto**. Dos tablas para el mismo hecho, y por eso `estado()` tenía que
    leer las dos, deduplicarlas por (sujeto, regla) y elegir cuál `abierto_at`
    mostrar — de ahí salieron «AHORA dice recién y ENCONTRÓ 11 días» y «ROTO
    AHORA muestra 8 filas que son 4».
    
    Ahora escribe por la MISMA puerta que todo lo demás (`registro.guardar`),
    que persiste la foto y el objeto juntos. La tabla vieja no se dropea
    —borrar código se revierte, borrar datos no— pero un test prohíbe escribirla.

    ⚠️ **Y OBSERVAR PASÓ A IR CON LA ESCRITURA.** El daemon late cada 30 s y
    corría `_observar()` (medido en prod: **1218 ms**, el barrido completo)
    en cada vuelta, para después persistir una de cada diez: la foto ya estaba
    throttleada a 5 minutos y la tabla propia era lo único que justificaba el
    resto. Al no haber tabla propia, nueve de cada diez pasadas eran trabajo que
    se tiraba. Ahora se observa cuando se escribe; el LATIDO sigue a 30 s,
    porque su trabajo es decir «estoy vivo» y eso sí tiene que ser de ahora.
    """
    t0 = time.perf_counter()
    from api.services import av_agent
    abierto = av_agent.en_rueda()
    err = ""
    nuevos = abiertos = fotos = 0
    observo = _toca_la_foto()
    try:
        if observo:
            hallazgos, evaluados = _observar()
            # Dedup por clave DENTRO de la pasada: dos detectores pueden ver el
            # mismo problema (un bono sin precio también sale sin TEA) y eso es
            # una fila, no dos.
            por_clave: dict[str, dict] = {}
            for h in hallazgos:
                por_clave.setdefault(_clave(h), h)
            r = _escribir_la_foto(list(por_clave.values()), evaluados)
            fotos, nuevos = r["foto"], r["nuevos"]
        # El conteo va SIEMPRE, haya observado o no: es lo que dibuja el número
        # del semáforo y no puede quedarse en el del último censo.
        abiertos = _contar_abiertos()
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
            "nuevos": nuevos, "ms": ms, "error": err, "foto": fotos,
            # ¿Esta vuelta miró, o solo latió? Sin esto, un ciclo de 4 ms y uno
            # de 1200 se leen igual en el log.
            "observo": observo}


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


# ⚠️⚠️ **FASE 3 — UNA SOLA TABLA** (2026-08-24). Acá había DOS lecturas y un
# reconciliador: `agente.av_agent_centinela` (lo que veía el daemon) y
# `agente.av_agent_items` (todo lo demás), pegadas con un LEFT JOIN, después
# deduplicadas a mano por (sujeto, regla) y con dos `abierto_at` de los que
# había que elegir uno. Cada una de esas costuras produjo un bug con nombre:
#
#   · «AHORA dice *recién* y ENCONTRÓ *11 días*» — dos relojes para un hecho
#   · «ROTO AHORA muestra 8 filas que son 4» — el mismo motor en las dos tablas
#   · el JOIN por `lower(sujeto) || '|' || lower(regla)` — la tercera
#     implementación de la identidad, que fallaba en silencio
#
# Ninguno se arregló entendiendo mejor la costura: se arreglaron sacando la
# costura. La tabla vieja NO se dropea (borrar código se revierte, borrar datos
# no) y un test prohíbe volver a escribirla.
_COLS = ["clave", "tipo", "sujeto", "regla", "severidad", "titulo",
         "datos", "abierto_at", "ultimo_at", "veces", "visto_at",
         "resuelto_at", "resuelto_como", "reaperturas", "vuelto_at", "estado"]

# Qué tipos son «del daemon» a los ojos de esta pantalla. Sale de `_CUBRE` (lo
# que el daemon declara vigilar) más `EN_AHORA_SIEMPRE` (lo que va a AHORA sea
# de quien sea): las dos listas ya existían y se leen de donde están, porque una
# tercera acá se separaría de las otras sin dar ningún error.
def _tipos_de_la_pantalla() -> list[str]:
    from api.services.av_agent import EN_AHORA_SIEMPRE
    return sorted({t for ts in _CUBRE.values() for t in ts} | set(EN_AHORA_SIEMPRE))


def _fila(r) -> dict:
    """Una fila de `av_agent_items` con la forma que la pantalla ya dibuja.

    El renombre vive ACÁ y en un solo lugar: la tabla canónica llama `titulo` a
    lo que el centinela llamaba `motivo` y `datos` a lo que llamaba `evidencia`.
    """
    f = dict(zip(_COLS, r, strict=True))
    d = f.pop("datos", None) or {}
    f["motivo"] = f.pop("titulo", "")
    f["evidencia"] = d
    # El texto LARGO (qué pasó · a qué afecta · si sigue) y la VENTANA que la
    # pieza declaró. Los motores los traen desde siempre; la pantalla mostraba
    # solo el título recortado — el user: *«sin información, sin contexto»*.
    f["detalle"] = str(d.get("texto") or "")
    f["muestra"] = str(d.get("muestra") or "")[:400]
    f["ventana"] = d.get("ventana")
    # `estado` no viaja a la pantalla: lo que dibuja son las marcas de tiempo,
    # y publicar los dos invitaría a que el front derive el suyo (REGLA #9).
    f.pop("estado", None)
    return f


def estado(limite: int = 200) -> dict:
    """El tablero del centinela: el latido + lo abierto + lo que se arregló solo.

    UN request: la pantalla no puede pedir tres cosas para dibujar un círculo.
    """
    fuera = {"ok": False, "vivo": False, "abiertos": [], "resueltos": [],
             "latido": None}
    cols = ", ".join(_COLS)
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT at, ciclo, en_rueda, abiertos, nuevos, "
                        "       duracion_ms, error, proximo_en_s "
                        "FROM agente.av_agent_latido WHERE id")
            lat = cur.fetchone()
            tipos = _tipos_de_la_pantalla()
            cur.execute(
                f"SELECT {cols} FROM agente.av_agent_items "
                " WHERE estado NOT IN ('resuelto', 'ignorado') "
                "   AND tipo = ANY(%s) "
                # Lo NUEVO y sin ver primero: es lo único que pide una decisión.
                " ORDER BY (visto_at IS NULL) DESC, "
                "  CASE severidad WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, "
                "  abierto_at DESC LIMIT %s", (tipos, limite))
            abiertos = [_fila(r) for r in cur.fetchall()]
            # Lo que se arregló SOLO en las últimas horas. Sirve para dos cosas:
            # confirmar que algo que estabas por atender ya no está, y ver los
            # intermitentes (los que se resuelven y vuelven).
            cur.execute(
                f"SELECT {cols} FROM agente.av_agent_items "
                " WHERE estado = 'resuelto' AND tipo = ANY(%s) "
                "   AND resuelto_at > now() - interval '8 hours' "
                " ORDER BY resuelto_at DESC LIMIT 40", (tipos,))
            resueltos = [_fila(r) for r in cur.fetchall()]
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
        # el reloj mientras dibuja (y porque el criterio es uno solo).
        #
        # ⚠️ Y ahora `abierto_at` es **el canónico y el único**: era la fecha del
        # objeto vs la de la tabla propia, con un `coalesce` en el medio para
        # decidir cuál mostrar. No hay cuál elegir.
        desde = f.get("abierto_at")
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
        # así cortados, no se entiende nada»*.
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


# ⚠️⚠️ **CADA BLOQUE PREGUNTA OTRA COSA, Y LA FECHA TIENE QUE CONTESTAR ESA.**
#
# El user, a las 11:18 mirando cuatro filas fechadas 10:32 (2026-08-24):
#
#   *«el aviso es de 10:32 sabiendo que son 11:18… o debería haber actualizado
#   el error si siguió estando, o debería haber desaparecido si ya se arregló.
#   Es todo demasiado estático para algo que supuestamente es live. No tienen
#   esa vida, que supuestamente es el estado del objeto.»*
#
# Y el objeto SÍ estaba vivo: el motivo de esas mismas filas decía «11:13». Lo
# que estaba mal era la FECHA de la fila, que salía de un `coalesce` fijo —
# `vuelto_at → resuelto_at → abierto_at`— en el que **`ultimo_at` no entraba
# nunca**. O sea que una fila re-confirmada hace dos minutos mostraba el día que
# NACIÓ, y por eso parecía muerta.
#
# El arreglo no es «mostrar `ultimo_at` en todos lados»: sería el mismo error al
# revés (APARECIÓ HOY con la hora de recién no dice cuándo apareció). Cada
# bloque hace UNA pregunta y hay UNA fecha que la contesta:
#
#     ROTO AHORA        ¿sigue roto?          → ultimo_at   (cuándo se confirmó)
#     NO LO PUDE VERIF. ¿desde cuándo no sé?  → ultimo_at   (la última señal)
#     VOLVIÓ            ¿cuándo volvió?       → vuelto_at
#     APARECIÓ HOY      ¿cuándo apareció?     → abierto_at
#     SE ARREGLÓ        ¿cuándo cerró?        → resuelto_at
#
# Se resuelve ACÁ y no en la pantalla por lo de siempre: el navegador no puede
# mirar el reloj mientras dibuja, y con el criterio del lado del front la
# consola y la tab se separan sin dar ningún error.
_CUANDO_POR_BLOQUE: dict[str, tuple[str, str]] = {
    "roto":          ("ultimo_at", "confirmado"),
    "sin_confirmar": ("ultimo_at", "última señal"),
    "volvio":        ("vuelto_at", "volvió"),
    "aparecio":      ("abierto_at", "apareció"),
    "se_arreglo":    ("resuelto_at", "cerró"),
}


def _fechar(bloque: str, filas: list[dict]) -> list[dict]:
    """Le pone a cada fila la fecha que su bloque necesita, ya elegida.

    `cuando` es el instante y `cuando_dice` es el verbo — porque «11:13» solo
    significa algo si al lado dice si eso es *confirmado* o *apareció*. La
    pantalla imprime los dos y no elige nada.
    """
    campo, verbo = _CUANDO_POR_BLOQUE.get(bloque, ("abierto_at", "desde"))
    out = []
    for f in filas:
        # Respaldo hacia `abierto_at`: una fila sin la fecha de su bloque no
        # puede quedar sin ninguna — pero el verbo lo DICE, así que no se
        # confunde una confirmación con un nacimiento.
        cuando = f.get(campo) or f.get("abierto_at")
        out.append({**f, "cuando": cuando,
                    "cuando_dice": verbo if f.get(campo) else "desde"})
    return out


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
    from api.services import av_agent_registro as registro
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
    # ⚠️⚠️ **Y SOLO SI ALGUIEN LO CONFIRMÓ RECIÉN** (2026-08-24). El user, con
    # cuatro motores en ROTO AHORA fechados tres días antes: *«es inaceptable
    # que AHORA muestre cosas que no sean del día actual»*.
    #
    # No había ningún bug en la cadena, y eso es lo que lo hacía invisible. Un
    # detector que levanta —o que ni corre, porque `relevar_live` solo se llama
    # en rueda— no declara su tipo en `evaluados`, y entonces **nada suyo se
    # cierra por ausencia**. Eso está bien y es deliberado (§0.be): cerrar sin
    # haber mirado es la mentira optimista que deja el tablero en verde el día
    # que está más ciego.
    #
    # Pero la fila queda abierta con su motivo congelado, y esta función la
    # publica afirmando **«está roto AHORA»**. No lo sabe: sabe que estaba roto
    # la última vez que alguien pudo mirar. El guard evita el falso verde y
    # produce un falso rojo eterno — con el latido en verde al lado, porque el
    # daemon sí está vivo. Vivo y ciego a la vez.
    #
    # Las dos mitades son la misma ley y solo estaba escrita una:
    #     al ESCRIBIR   «no miré» ≠ «no hay nada»     → no cerrar
    #     al LEER       «no miré» ≠ «sigue pasando»   → no afirmar
    #
    # Y no se ESCONDE: eso sería el silencio que se lee como verde (§0.s). Lo
    # que no se pudo confirmar sale a su propio bloque, con el detector y desde
    # cuándo no da señales — un fantasma declarado deja de ser un fantasma.
    conf = registro.confirmados()
    roto, sin_confirmar = [], []
    for f in abiertos:
        if not (va_en_ahora(f.get("tipo") or "") and _puede_pasar_hoy(f, habil)):
            continue
        edad = registro.sin_confirmar(f.get("tipo") or "", conf)
        if edad is None:
            roto.append(f)
        else:
            sin_confirmar.append({**f, "sin_confirmar_s": (
                None if edad == float("inf") else int(edad))})
    # `claves_roto` cubre A LOS DOS: la fila que no se pudo confirmar tampoco
    # puede reaparecer abajo como «apareció» — sigue siendo la misma fila, y
    # verla dos veces en la misma pantalla se lee como dos problemas.
    claves_roto = {f.get("clave") for f in roto + sin_confirmar}

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
        "roto": _fechar("roto", _por_hora(roto)),
        # Lo que SIGUE ABIERTO pero nadie pudo verificar hace rato. No es una
        # novedad y no suma al contador: es una advertencia sobre el AGENTE, no
        # sobre el sistema — «esto no lo estoy mirando».
        "sin_confirmar": _fechar("sin_confirmar", _por_hora(sin_confirmar)),
        "aparecio": _fechar("aparecio", _por_hora(aparecio)), "volvio": _fechar("volvio", _por_hora(volvio)),
        "se_arreglo": _fechar("se_arreglo", _por_hora(cerro)),
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

    ⚠️ **Desde la Fase 3 escribe en la tabla CANÓNICA.** Antes marcaba visto en
    la tabla propia del centinela y el objeto no se enteraba: la misma fila
    salía de NUEVO en AHORA y seguía contada como «sin ver» en ENCONTRÓ. Dos
    tablas, dos respuestas a «¿ya lo miré?», y ninguna de las dos equivocada
    por su cuenta — que es cómo se ven todos los bugs de este subsistema.
    """
    from api.services import av_agent_items
    return av_agent_items.marcar_vistos(claves, por=por)
