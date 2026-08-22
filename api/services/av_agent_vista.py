"""api/services/av_agent_vista.py — la vista /av-agent en UN request (E1.d).

Doc madre: **`docs/AV_AGENT.md`**.

Sirve la pantalla entera de una: última corrida de hallazgos + preguntas abiertas
+ lo ya decidido. Un solo request y no cinco, por la misma razón que la vista de
SENEBIS y la de ACA: cada roundtrip a Supabase paga un peaje fijo de ~8,5 ms de
DISTANCIA (Droplet en nyc1 ↔ base en us-east-1), así que lo que importa es la
CANTIDAD de queries, no su plan.

**Los hallazgos se sirven de la ÚLTIMA CORRIDA, no en vivo.** Relevar cuesta ~29
créditos y 1-2 minutos de throttle: hacerlo en el request de una pantalla que se
abre diez veces por día sería absurdo. La vista muestra la foto y dice **cuándo**
se sacó — que es la diferencia entre un dato viejo y un dato viejo que miente.
"""
from __future__ import annotations

import logging
from datetime import date

from api.cache import cached
from api.services import av_agent, av_agent_evals
from core import ciclo, curvas_ejes
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# ⚠️ `alcance` entra a la lista porque es parte de la IDENTIDAD del hallazgo
# como objeto (`av_agent_items.clave_de_problema`): sin él, el que lee calcula
# una clave distinta de la que escribió el detector y la memoria queda
# inalcanzable, existiendo. Mismo modo de falla que el símbolo columna-vs-blob.
_COLS_H = ["tipo", "ticker", "regla", "severidad", "motivo", "evidencia"]

# ── LA MEMORIA, EN LA MISMA QUERY ───────────────────────────────────────────
#
# La foto dice QUÉ hay hoy; esto dice **desde cuándo** y **cuántas veces**. Son
# datos que una foto no puede tener: cada corrida la reescribe entera, así que
# todo se veía «de hoy» aunque llevara dos semanas.
#
# Entra por `LEFT JOIN` y no por una query aparte porque **el peaje de Supabase
# se paga por VIAJE** (~8,5 ms), y hay un test que cuenta los viajes de esta
# función. El JOIN es por `h.clave`, que la escribe el detector: nadie
# recalcula la identidad (REGLA #9).
_COLS_MEM = ["veces", "abierto_at", "estado_item"]
_SELECT_H = (", ".join(f"h.{c}" for c in _COLS_H)
             + ", i.veces, i.abierto_at, i.estado")
_ORDEN_SEV = {"alta": 0, "media": 1, "baja": 2}


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── EL AVISO TAMBIÉN ES UN OBJETO (§0.bk) ───────────────────────────────────
#
# Un aviso es *«a este bono le falta el CER de emisión»*: exactamente
# **(qué cosa, qué le pasa)**, o sea la misma identidad que un hallazgo. Que lo
# haya visto el alta y no el detector nocturno no lo convierte en otra cosa
# (§0.bj) — y de hecho, cuando el detector encuentra ese mismo dato faltando,
# los dos escriben en el MISMO objeto. Que se junten es lo correcto: es un solo
# problema.
#
# Lo que gana con esto es lo que la tabla no puede dar: **desde cuándo** está
# pendiente, **cuántas veces** volvió a hacer falta avisar, y el seguimiento
# escalonado cuando se cierra.
#
# ⚠️ La tabla `av_agent_avisos` NO se toca. Sigue siendo la que arma la lista
# con su campo para tipear, su vencimiento y su dueño. El objeto acompaña: si el
# espejo falla, el aviso existe igual — un problema de memoria no puede tumbar
# la pantalla de pendientes.


def _sujeto_aviso(ticker: str, para: str = "") -> str:
    """Un aviso de bono habla del BONO; un mensaje a una persona, de esa
    persona. Sin esto, los mensajes sin ticker caerían todos en un mismo objeto
    y se taparían entre ellos."""
    return (ticker or "").strip() or (para or "").strip().lower()


def _espejar_aviso(*, ticker: str, clave: str, que_hacer: str,
                   para: str = "", severidad: str = "media") -> None:
    """El aviso, como objeto. Nunca levanta: es memoria, no es la lista."""
    try:
        from api.services import av_agent_items
        av_agent_items.ver(
            tipo="aviso", origen="aviso",
            sujeto=_sujeto_aviso(ticker, para), regla=clave,
            titulo=(que_hacer or "")[:300],
            afecta=("Manager → Títulos" if not para else f"le toca a {para}"),
            severidad=severidad, datos={"para": para} if para else {})
    except Exception as e:                  # pragma: no cover - defensivo
        logger.warning("av_agent: no pude espejar el aviso %s/%s (%s)",
                       ticker, clave, e)


def _cerrar_aviso(ticker: str, clave: str, *, por: str = "",
                  para: str = "", reabrir: bool = False) -> None:
    """El cierre humano, en el objeto.

    ⚠️ **Reabrir es `volvio`, no «nuevo».** El vocabulario solo deja salir de
    `resuelto` por ahí, y además es lo que pasó de verdad: alguien lo había
    dado por hecho y el pendiente sigue. Borrar esa vuelta sería regalarle
    confianza a un arreglo que no fue (el seguimiento la cuenta).
    """
    try:
        from api.services import av_agent_items
        from core import ciclo
        k = av_agent_items.clave_de_problema(_sujeto_aviso(ticker, para),
                                             clave, "aviso")
        av_agent_items.marcar(k, ciclo.VOLVIO if reabrir else ciclo.RESUELTO,
                              por=por or "persona")
    except Exception as e:                  # pragma: no cover - defensivo
        logger.warning("av_agent: no pude cerrar el objeto del aviso %s/%s (%s)",
                       ticker, clave, e)


def crear_avisos(ticker: str, pasos: list[dict], por: str = "") -> int:
    """Anota los avisos que dejó un alta. Se llama al APLICAR, no al simular.

    `ON CONFLICT DO NOTHING` sobre el índice parcial de abiertos: re-aplicar el
    mismo bono no duplica la fila, pero si el user ya cerró ese aviso y el
    problema reaparece, el nuevo SÍ entra."""
    filas = [(ticker, p["clave"], p["aviso"], p.get("detalle", "")[:300],
              "Manager → Títulos", por or None)
             for p in pasos if p.get("aviso")]
    if not filas:
        return 0
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO agente.av_agent_avisos "
                "(ticker, clave, que_hacer, por_que, donde, creado_por) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING", filas)
        for f in filas:
            _espejar_aviso(ticker=f[0], clave=f[1], que_hacer=f[2])
        return len(filas)
    except Exception as e:
        logger.warning("av_agent: no se pudieron anotar los avisos de %s: %s", ticker, e)
        return 0


def resolver_aviso(aviso_id: int, por: str = "", deshacer: bool = False) -> dict:
    """Marca el aviso como hecho (o lo reabre). **Lo cierra una PERSONA**, que es
    justo lo que pidió el user: el aviso es su lista de tareas, no un derivado."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE agente.av_agent_avisos SET resuelto = %s, "
                "resuelto_por = %s, resuelto_at = CASE WHEN %s THEN NULL ELSE now() END "
                "WHERE id = %s RETURNING ticker, clave",
                (not deshacer, (por or None) if not deshacer else None,
                 deshacer, aviso_id))
            fila = cur.fetchone()
        if not fila:
            return {"ok": False, "error": "no existe ese aviso"}
        _cerrar_aviso(fila[0], fila[1], por=por, reabrir=deshacer)
        return {"ok": True, "ticker": fila[0], "clave": fila[1],
                "resuelto": not deshacer}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# Qué condición del master corresponde a cada aviso. Es lo que permite decir
# `ya_cargado` sin que el aviso DEPENDA de eso para existir.
_COND_AVISO = {
    "cer_emision": lambda r: (_num(r["cer_emision"]) or 0) > 0,
    "emisor": lambda r: bool((r["emisor"] or "").strip()),
}

# **CÓMO SE COMPLETA cada aviso desde la propia lista** (pedido del user,
# 2026-08-17): *«que quede con el campo a completar desde ahí MISMO… escribo el
# valor y ya entiende cómo guardarlo en la base y lo usa para terminar de
# simular»*. Sin esto el aviso te dice qué falta y te manda a otra pantalla a
# buscar el bono, lo cual es la mitad del trabajo hecha.
#
# Vive PEGADO a `_COND_AVISO` a propósito: son las dos caras del mismo dato —una
# lo escribe y la otra verifica que quedó escrito— y separarlas es cómo se
# consigue que un aviso se pueda completar pero nunca se marque cargado.
_CAMPO_AVISO = {
    "cer_emision": {
        "label": "CER de emisión",
        "tipo": "numero",
        "ayuda": "el índice CER del día de emisión del bono (ej. 12,3456)",
        # `data` es un jsonb y `cer_emision` vive adentro, no en columna.
        "sql": "UPDATE mercado.curvas SET data = jsonb_set(COALESCE(data, '{}'::jsonb), "
               "'{cer_emision}', to_jsonb(%(valor)s::numeric)) WHERE ticker = %(ticker)s",
        "cast": "numero",
    },
    "emisor": {
        "label": "Emisor",
        "tipo": "texto",
        "ayuda": "el nombre del emisor tal como lo escribe 1816",
        # En DOS lugares, como `jobs/ficha_1816`: la columna y el blob. Si se
        # escribe uno solo, agrupar por emisor da distinto según de dónde se lea.
        "sql": "UPDATE mercado.curvas SET emisor = %(valor)s, "
               "data = jsonb_set(COALESCE(data, '{}'::jsonb), '{emisor}', "
               "to_jsonb(%(valor)s::text)) WHERE ticker = %(ticker)s",
        "cast": "texto",
    },
}


def completar_aviso(aviso_id: int, valor, por: str = "") -> dict:
    """Escribe el dato que faltaba **y cierra el aviso en el mismo acto.**

    Dos cosas que NO hace, a propósito:

    - **No inventa el campo.** Solo se puede completar lo que está en
      `_CAMPO_AVISO`; una clave desconocida devuelve error en vez de escribir
      algo parecido. El aviso sigue ahí para cerrarlo a mano.
    - **No confía en que escribió.** Después del UPDATE relee con el MISMO
      predicado de `_COND_AVISO`, y si el dato no quedó cargado **no cierra el
      aviso**: cerrar sin verificar es exactamente el «marcado hecho + dato
      ausente» que `ya_cargado` existe para cazar.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker, clave, resuelto FROM agente.av_agent_avisos "
                        "WHERE id = %s", (aviso_id,))
            fila = cur.fetchone()
            if not fila:
                return {"ok": False, "error": "no existe ese aviso"}
            ticker, clave, _resuelto = fila
            campo = _CAMPO_AVISO.get(clave)
            if not campo:
                return {"ok": False, "error": f"el aviso «{clave}» no se completa "
                                              "desde acá: hay que cargarlo en Manager"}
            if campo["cast"] == "numero":
                v = _num(valor)
                if v is None or v <= 0:
                    return {"ok": False, "error": "el valor tiene que ser un número "
                                                  "mayor que cero"}
            else:
                v = str(valor or "").strip()
                if not v:
                    return {"ok": False, "error": "el valor no puede estar vacío"}

            cur.execute(campo["sql"], {"valor": v, "ticker": ticker})
            if not cur.rowcount:
                return {"ok": False, "error": f"{ticker} no está en mercado.curvas — "
                                              "hay que darlo de alta primero"}

            # VERIFICAR, no suponer: se relee con el mismo predicado que usa la
            # lista para decir `ya_cargado`.
            cur.execute("SELECT data->>'cer_emision', emisor FROM mercado.curvas "
                        "WHERE ticker = %s", (ticker,))
            r = cur.fetchone() or (None, None)
            cond = _COND_AVISO.get(clave)
            quedo = bool(cond({"cer_emision": r[0], "emisor": r[1]})) if cond else True
            if not quedo:
                return {"ok": False, "error": "el UPDATE corrió pero el dato no "
                                              "quedó cargado — no se cierra el aviso"}
            cur.execute("UPDATE agente.av_agent_avisos SET resuelto = true, "
                        "resuelto_por = %s, resuelto_at = now() WHERE id = %s",
                        ((por or None), aviso_id))
        return {"ok": True, "ticker": ticker, "clave": clave, "valor": v,
                "resuelto": True}
    except Exception as e:
        logger.warning("av_agent: no se pudo completar el aviso %s: %s", aviso_id, e)
        return {"ok": False, "error": str(e)[:200]}


def avisos(incluir_resueltos: bool = True) -> list[dict]:
    """**Lo que quedó para hacer A MANO.** Pedido del user (2026-08-17):

        *«Está bien que se cargue sin CER de emisión. Solamente tiene que haber
        una sección acá en el agente que se llame AVISOS, y todo lo que aparezca
        ahí es para hacer manual… y solo desaparezca cuando yo marque el aviso
        como ejecutado.»*

    Es el cambio de postura que hace útil al agente: en vez de negarse a hacer el
    95% del trabajo porque no puede hacer el 5%, **hace el 95% y deja anotado el
    5%**.

    **Se PERSISTE y lo cierra una persona.** Mi primer diseño lo derivaba del
    estado del master —cargás el dato y la fila se va sola— y estaba mal por dos
    razones que el user vio antes que yo: el aviso es SU lista de tareas, y una
    lista que se borra sola no deja ver qué había pendiente ni qué se hizo.

    **Pero el cierre manual solo es seguro si algo lo contrasta**, si no un aviso
    marcado como hecho sobre un dato que sigue faltando miente en silencio. Por
    eso cada fila viaja con **`ya_cargado`**: el cruce contra el master en vivo,
    en la misma query. Marcado hecho + dato ausente = la pantalla lo canta.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"""
                SELECT a.id, a.ticker, a.clave, a.que_hacer, a.por_que, a.donde,
                       a.creado_at, a.resuelto, a.resuelto_por, a.resuelto_at,
                       c.data->>'cer_emision', c.emisor, (c.ticker IS NOT NULL),
                       a.para
                  FROM agente.av_agent_avisos a
                  LEFT JOIN mercado.curvas c ON c.ticker = a.ticker
                 -- ⚠️ **SOLO LOS AVISOS DE BONOS.** Esta sección se construyó
                 -- para una cosa: «di de alta el bono y le falta un dato,
                 -- cargalo acá». Desde que la misma tabla guarda MENSAJES a
                 -- personas, los mensajes caían acá y se dibujaban con esa
                 -- plantilla — el aviso de saldos salía bajo la columna BONO
                 -- diciendo «cargá el dato», que no significa nada.
                 --
                 -- El corte es por `clave`: las de `_CAMPO_AVISO` son datos de
                 -- bono y tienen dónde tipearse. Lo demás son mensajes y se ven
                 -- aparte. Se filtra por la lista DECLARADA y no por «el ticker
                 -- empieza con tema:», que sería adivinar por el string.
                 WHERE a.clave = ANY(%s)
                 {'' if incluir_resueltos else 'AND NOT a.resuelto'}
                 ORDER BY a.resuelto, a.creado_at DESC
            """, (list(_CAMPO_AVISO),))
            filas = cur.fetchall()
    except Exception as e:
        logger.warning("av_agent: no se pudieron leer los avisos: %s", e)
        return []

    out: list[dict] = []
    for (aid, ticker, clave, que, porque, donde, creado, resuelto,
         rpor, rat, cer, emisor, en_curvas, para) in filas:
        cond = _COND_AVISO.get(clave)
        ya = bool(cond({"cer_emision": cer, "emisor": emisor})) if (cond and en_curvas) \
            else None
        out.append({
            "id": aid, "ticker": ticker, "clave": clave, "que_hacer": que,
            "por_que": porque, "donde": donde,
            "creado_at": creado.isoformat() if creado else None,
            "resuelto": resuelto, "resuelto_por": rpor, "para": para,
            "resuelto_at": rat.isoformat() if rat else None,
            # Si este aviso se puede completar SIN salir de la lista, viaja acá
            # cómo pedirlo. `None` = hay que ir a Manager.
            "campo": ({"label": _CAMPO_AVISO[clave]["label"],
                       "tipo": _CAMPO_AVISO[clave]["tipo"],
                       "ayuda": _CAMPO_AVISO[clave]["ayuda"]}
                      if clave in _CAMPO_AVISO and en_curvas else None),
            # `None` = no sabemos verificarlo (aviso sin condición conocida, o el
            # bono ya no está en el master). No se inventa un True.
            "ya_cargado": ya,
        })
    return out


def avisar_a(*, para: str, ticker: str, clave: str, que_hacer: str,
             por_que: str = "", donde: str = "", por: str = "") -> int:
    """**El ping a una persona.** Deja un pendiente con dueño.

    Es la MISMA fila que el resto de los avisos: se cierra igual, se ve igual y
    se audita igual. Lo único distinto es `para`.

    `ON CONFLICT DO NOTHING` sobre el índice de abiertos: avisar dos veces por
    lo mismo no genera dos avisos. Un aviso repetido no informa más — informa
    menos, porque enseña a ignorar la lista. Si la persona ya lo cerró y el
    problema vuelve a aparecer, el nuevo SÍ entra (el índice es parcial sobre
    los no resueltos), que es justo cuando avisar de nuevo sí significa algo.

    Devuelve 1 si se creó, 0 si ya estaba abierto.
    """
    email = (para or "").strip().lower()
    if "@" not in email:
        raise ValueError("hay que elegir a quién avisarle")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agente.av_agent_avisos "
            "(ticker, clave, que_hacer, por_que, donde, creado_por, para) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (ticker, clave, que_hacer[:500], (por_que or "")[:300],
             donde or None, por or None, email))
        n = cur.rowcount
    _espejar_aviso(ticker=ticker, clave=clave, que_hacer=que_hacer, para=email)
    return n


def mensajes(limite: int = 40) -> list[dict]:
    """**Los mensajes que el agente le mandó a alguien.** Minimalista: a quién,
    qué, cuántas filas quedan sin marcar y si ya se cerró.

    Va aparte de `avisos()` porque son dos cosas distintas que compartían tabla:
    un aviso de bono se COMPLETA acá (tiene su campo para tipear); un mensaje se
    MANDÓ y lo resuelve otra persona en su pantalla. Mezclarlos hacía que el
    aviso de saldos apareciera bajo la columna BONO pidiendo «cargá el dato».

    El user pidió verlo todo en el agente igual — *«de manera minimalista pero
    todo registrado»*: el agente tiene que poder mostrar qué mandó y si lo
    atendieron, sin convertirse en la bandeja de otro.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT a.id, a.para, a.clave, a.que_hacer, a.creado_at, "
                "       a.resuelto, a.resuelto_at, a.vence_at, "
                "       count(i.id), count(i.id) FILTER (WHERE i.hecho) "
                "FROM agente.av_agent_avisos a "
                "LEFT JOIN agente.av_agent_aviso_items i ON i.aviso_id = a.id "
                "WHERE a.clave <> ALL(%s) AND a.para IS NOT NULL "
                "GROUP BY a.id ORDER BY a.creado_at DESC LIMIT %s",
                (list(_CAMPO_AVISO), limite))
            filas = cur.fetchall()
    except Exception as e:
        logger.warning("av_agent: no se pudieron leer los mensajes (%s)", e)
        return []
    return [{"id": r[0], "para": r[1], "tema": r[2], "asunto": r[3],
             "creado_at": r[4].isoformat() if r[4] else None,
             "resuelto": r[5],
             "resuelto_at": r[6].isoformat() if r[6] else None,
             "vence_at": r[7].isoformat() if r[7] else None,
             "filas": int(r[8] or 0), "hechas": int(r[9] or 0)} for r in filas]


def avisos_de(email: str) -> list[dict]:
    """Los pendientes ABIERTOS de una persona. Es lo que verifica que el ping
    llegó, y lo que la pantalla usa para mostrarle a cada uno lo suyo."""
    e = (email or "").strip().lower()
    if not e:
        return []
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, ticker, clave, que_hacer, por_que, donde, creado_at, "
                "       interrumpe, vence_at "
                "FROM agente.av_agent_avisos "
                "WHERE NOT resuelto AND lower(para) = %s "
                # ⚠️ **UN AVISO VENCIDO NO SE MUESTRA.** El de saldos vale HOY;
                # mañana el mercado abre con otros números y pedir acción sobre
                # la foto de ayer es peor que no avisar. Los que no vencen
                # (`NULL`) siguen como siempre.
                "  AND (vence_at IS NULL OR vence_at > now()) "
                "ORDER BY interrumpe DESC, creado_at DESC",
                (e,))
            cols = [d[0] for d in cur.description]
            filas = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
            # Las FILAS de los avisos que son una tabla. En la MISMA conexión:
            # el peaje se paga por viaje.
            ids = [f["id"] for f in filas]
            items: dict[int, list[dict]] = {}
            if ids:
                cur.execute(
                    "SELECT id, aviso_id, etiqueta, datos, hecho, hecho_at "
                    "FROM agente.av_agent_aviso_items "
                    "WHERE aviso_id = ANY(%s) ORDER BY aviso_id, orden", (ids,))
                for i, av, etq, datos, hecho, hat in cur.fetchall():
                    items.setdefault(av, []).append({
                        "id": i, "etiqueta": etq, "datos": datos or {},
                        "hecho": hecho,
                        "hecho_at": hat.isoformat() if hat else None})
            for f in filas:
                f["items"] = items.get(f["id"], [])
                f["pendientes"] = sum(1 for x in f["items"] if not x["hecho"])
    except Exception as ex:
        logger.warning("av_agent: no se pudieron leer los avisos de %s: %s", e, ex)
        return []
    for f in filas:
        for k in ("creado_at", "vence_at"):
            c = f.get(k)
            f[k] = c.isoformat() if hasattr(c, "isoformat") else None
    return filas


def resolver_aviso_propio(aviso_id: int, *, quien: str) -> dict:
    """Cierra un aviso **solo si es de esa persona**.

    El `AND lower(para) = %s` va en el WHERE y no en un `if` previo a propósito:
    así "es mío" no es un permiso que alguien pueda olvidarse de chequear en el
    próximo endpoint que toque esta tabla — es parte de la escritura. Un id ajeno
    no falla con "no autorizado" sino con "no existe", que además no confirma que
    ese aviso exista.
    """
    e = (quien or "").strip().lower()
    if not e:
        return {"ok": False, "error": "sin identidad"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE agente.av_agent_avisos SET resuelto = true, "
                "resuelto_por = %s, resuelto_at = now() "
                "WHERE id = %s AND lower(para) = %s AND NOT resuelto "
                "RETURNING ticker, clave", (e, aviso_id, e))
            fila = cur.fetchone()
    except Exception as ex:
        logger.warning("av_agent: no se pudo cerrar el aviso %s: %s", aviso_id, ex)
        return {"ok": False, "error": str(ex)[:200]}
    if not fila:
        return {"ok": False, "error": "no existe ese aviso"}
    _cerrar_aviso(fila[0], fila[1], por=e, para=e)
    return {"ok": True, "ticker": fila[0], "clave": fila[1], "resuelto": True}


@cached(ttl=45)
def _salud_por_id() -> dict[str, dict]:
    """`{id del chequeo: el chequeo VIVO ENTERO}` — no el de la foto.

    Cacheado 45s: menos que el poll de la pantalla, así que lo que se arregla se
    ve en la lectura siguiente, y suficiente para que abrir el modal diez veces
    no pague diez veces la evaluación entera.

    ⚠️ Devuelve `{}` si no se pudo evaluar — y `_caduco` trata el id ausente como
    **no resuelto**. Un fallo de SALUD no puede vaciar la pantalla: sería
    exactamente la mentira que este agente existe para no decir.

    ⚠️⚠️ **DEVUELVE EL CHEQUEO ENTERO Y NO SOLO EL ESTADO, y ese fue el bug.**
    La primera versión traía `{id: estado}`, con lo cual el cotejo solo sabía
    contestar UNA pregunta: ¿está en verde? Y un control que pasa de **8 casos a
    3** no está en verde — sigue rojo, con toda la razón. Así que la fila se
    quedaba, con su texto congelado diciendo «8 anomalías sin resolver», y el
    user arreglaba 5 cosas de verdad y la pantalla mostraba **el mismo número,
    dígito por dígito**.

    El estado sirve para TACHAR la fila; el chequeo entero sirve para
    **REESCRIBIRLA**. Son dos usos distintos del mismo cotejo y el segundo es el
    que hace visible el progreso parcial, que es el 90% del trabajo real: casi
    nada se arregla de una.
    """
    try:
        from api.services import salud
        return {str(c.get("id") or ""): c
                for c in (salud.evaluar() or []) if c.get("id")}
    except Exception as e:
        logger.warning("vista: no pude leer el estado vivo de SALUD (%s)", e)
        return {}


@cached(ttl=45)
def _controles_en_verde() -> set[str]:
    """Los controles que HOY no tienen ni un caso activo.

    ⚠️⚠️ **EL COTEJO GENERAL, en vez de un `if` por regla.** Se aplicaron los 6
    `pata_equivocada`, el control `patas_equivocadas` quedó en **0** y ENCONTRÓ
    siguió mostrando 17. Es la tercera vez en la sesión con la misma forma
    —control verde, hallazgo vivo— y hasta ahora se venía tapando de a una
    regla por vez (`sin_espejo_en_assets`), que es cómo se llega a la cuarta.

    La relación regla → control **ya existe** y no hace falta escribirla: cada
    `Accion` declara `causa` (la regla del hallazgo) y `sobre` (el control que
    resuelve). Se deriva de ahí, así una acción nueva trae su cotejo puesto.

    ⚠️ **Solo cuenta el control en CERO, a propósito.** Con casos activos se
    podría intentar matchear sujeto por sujeto, pero las claves no siempre son
    el mismo string —`assets_ticker_partido` guarda la UNIDAD y el hallazgo
    habla del TICKER— y un match fallido se leería como «resuelto». Cero activos
    no tiene esa ambigüedad: si el control no encuentra nada, ninguno de sus
    hallazgos puede seguir siendo cierto.
    """
    try:
        from api.services.controles_sql import listar_controles
        data = listar_controles(incluir_resueltos_dias=0)
    except Exception as e:
        logger.warning("vista: no pude leer los controles (%s)", e)
        return set()          # «no pude mirar» nunca es «está resuelto»
    return {cid for cid, g in (data.get("controles") or {}).items()
            if not (g.get("activos") or [])}


def _control_de_la_regla(regla: str) -> str:
    """El control que resuelve esa regla, según el registro de acciones."""
    try:
        from api.services.av_agent_hacer import ACCIONES
        for a in ACCIONES.values():
            if getattr(a, "causa", "") == regla:
                return getattr(a, "sobre", "") or ""
    except Exception:         # pragma: no cover - la pantalla no se cae por esto
        logger.warning("vista: no pude leer el registro de acciones", exc_info=True)
    return ""


@cached(ttl=45)
def _con_ficha() -> set[str] | None:
    """Los tickers con ficha en `assets`, cacheados para la PANTALLA.

    El predicado es `av_agent.tickers_con_ficha` —el mismo del detector— y lo
    único que agrega esto es la cache. Va acá y no en `av_agent` a propósito: el
    detector corre una vez por noche y tiene que leer fresco; la pantalla se abre
    muchas veces por día y cada viaje a Supabase cuesta ~8,5 ms de pura
    distancia. 45 s es menos que el poll, así que lo que se corrige se ve en la
    lectura siguiente.
    """
    return av_agent.tickers_con_ficha()


def _hallazgos_ultima_corrida() -> tuple[list[dict], str | None]:
    """Los hallazgos de la corrida MÁS RECIENTE + su timestamp.

    Se filtra por `corrida_at = (SELECT max(...))` y no por fecha: dos corridas
    del mismo día son dos fotos distintas, y mezclarlas mostraría hallazgos que
    ya se arreglaron al lado de los actuales.

    ⚠️ **Y se descarta lo que YA se arregló desde esa corrida** (2026-08-17). El
    user: *«detecté algo tremendo: hay bonos que siguen apareciendo… pero el M8
    de CER ya lo había agregado»*. Tenía razón y su intuición del motivo también
    («entiendo que es porque no se ejecutó de nuevo»): la lista es una FOTO y
    relevar de nuevo cuesta ~29 créditos y 1-2 minutos de throttle, así que no se
    puede hacer en cada apertura de la pantalla.

    **Pero mostrar como faltante un bono que el agente mismo acaba de crear
    destruye la confianza en toda la lista** — si una fila está mal, ninguna vale.
    La salida no es rehacer la foto: es **contrastarla contra la realidad antes de
    mostrarla**. Un `SELECT ticker FROM mercado.curvas` (una query, el peaje fijo
    de ~8,5 ms) alcanza para tachar los `falta_en_base` que ya existen. Es el
    mismo principio que el `ya_cargado` de los avisos: la foto se muestra, pero
    nunca sin cotejarla.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        # ⚠️ **El máximo se calcula EXCLUYENDO los alcances de REEMPLAZO**
        # (`live` y `sistema` — ver `av_agent.ALCANCES_VIVOS`). `jobs.av_agent_live`
        # reescribe sus hallazgos cada 5 minutos con `corrida_at = now()`, así
        # que un `max(corrida_at)` a secas devolvería SIEMPRE la corrida del
        # monitor y la relevada nocturna entera desaparecería de la pantalla —
        # en silencio, que es la peor forma. Son dos ciclos distintos conviviendo
        # en la misma tabla: el nocturno tiene UNA corrida vigente, el live es
        # un reemplazo permanente y no tiene corrida que elegir.
        vivos = list(av_agent.ALCANCES_VIVOS)
        cur.execute("SELECT max(corrida_at) FROM agente.av_agent_hallazgos "
                    "WHERE alcance <> ALL(%s)", (vivos,))
        fila = cur.fetchone()
        corrida = fila[0] if fila else None
        # **Y los de reemplazo VENCEN.** Un hallazgo de rueda es una foto del
        # momento: al cerrar el mercado el monitor deja de correr y su última
        # foto —la de las 16:55— se quedaba en la pantalla toda la noche y todo
        # el fin de semana. Eso es lo que el user veía como «avisos de sin precio
        # con el mercado cerrado»: el detector estaba bien, la foto estaba vieja.
        # El filtro va en SQL y no en Python para que el que consulte por otra
        # vía tampoco pueda leer una foto vencida.
        # ⚠️ **EL VENCIMIENTO ES POR REGLA, NO POR ALCANCE** (2026-08-19).
        #
        # Antes vencía el alcance entero, así que el monitor de rueda se llevaba
        # puesto todo lo suyo — incluidos los PROBLEMAS DE CONFIGURACIÓN (nadie
        # suscribe este símbolo, el master apunta a la pata equivocada). El user:
        # *«eso tiene que ser independiente del mercado… mañana va a volver a
        # abrir y va a pasar lo mismo»*.
        #
        # El costo no se veía: el problema desaparecía a la noche y volvía a la
        # mañana como nuevo, así que **nunca acumulaba antigüedad**. Uno de hace
        # tres semanas y uno de recién se veían igual.
        #
        # Ahora vence solo lo que es una foto del momento
        # (`av_agent.OBSERVACIONES_DE_MERCADO`); lo demás se sostiene hasta que la
        # próxima corrida lo reemplace o alguien lo arregle.
        # Y hay un TERCER caso, más corto todavía: **las buenas noticias**
        # (§0.ah). «MOTOR_X VOLVIÓ» informa por media hora; a las tres horas
        # ocupa el lugar de lo que sí está pasando ahora. Una buena noticia
        # envejece más rápido que una mala.
        obs = list(av_agent.OBSERVACIONES_DE_MERCADO)
        rapido = list(av_agent.VENCE_RAPIDO)
        cur.execute(
            f"SELECT {_SELECT_H} FROM agente.av_agent_hallazgos h "
            "LEFT JOIN agente.av_agent_items i ON i.clave = h.clave "
            "WHERE (%s IS NOT NULL AND h.corrida_at = %s AND h.alcance <> ALL(%s)) "
            "   OR (h.alcance = ANY(%s) AND ("
            #     una buena noticia: dura poco
            "         (h.regla = ANY(%s) AND h.corrida_at > now() - "
            "             make_interval(secs => %s))"
            #     una observación de mercado: solo vale si es reciente
            "      OR (h.regla = ANY(%s) AND h.corrida_at > now() - "
            "             make_interval(secs => %s))"
            #     un problema nuestro: sigue siendo cierto con el mercado cerrado
            "      OR (h.regla <> ALL(%s) AND h.regla <> ALL(%s))))",
            (corrida, corrida, vivos, vivos,
             rapido, av_agent.VENCE_RAPIDO_S,
             obs, av_agent.VENCE_OBSERVACION_S, obs, rapido))
        filas = [dict(zip(_COLS_H + _COLS_MEM, r, strict=False))
                 for r in cur.fetchall()]
        # El blob completo, no solo la PK: `sin_flujo` caduca cuando el bono YA
        # tiene cronograma, y eso se lee acá mismo. **Es la misma query** — el
        # peaje de Supabase se paga por viaje, no por columna.
        # `instrumento` (la COLUMNA, la que gana sobre el blob — §0.y) viaja en
        # la misma query: el cotejo de `pata_equivocada` compara el símbolo que
        # el master usa HOY contra el sugerido del hallazgo.
        cur.execute("SELECT ticker, data, instrumento FROM mercado.curvas")
        _filas_curvas = cur.fetchall()
        docs_curvas = {(r[0] or "").strip().upper(): (r[1] or {})
                       for r in _filas_curvas}
        simbolo_master = {(r[0] or "").strip().upper(): (r[2] or "").strip()
                          for r in _filas_curvas}
        en_curvas = set(docs_curvas)



    # ⚠️ **Cada tipo caduca por su PROPIA razón, y el campo `ticker` no significa
    # lo mismo en todos.** Acá se aplicaba UN predicado a dos tipos distintos: en
    # un `hueco_de_curva` el `ticker` es el **AJUSTE** (`BADLAR`), no un bono, así
    # que compararlo contra `mercado.curvas.ticker` no lo sacaba nunca — el user
    # lo vio con BADLAR, cuya curva el agente YA había creado.
    #
    # `ajuste_sin_curva` es la MISMA función que lo detecta y ya lee el catálogo,
    # así que preguntarle de nuevo no puede dar un criterio distinto.
    # El universo de Primary, cacheado 600s → pedirlo por lectura es gratis.
    # ⚠️ **LA CONFIANZA MEDIDA, y por qué está CACHEADA y no en el SELECT de
    # arriba.** Hay un test que cuenta las queries de esta función, y tiene
    # razón: cada una paga ~8,5 ms de distancia aunque ejecute en 0,1 ms, y esta
    # pantalla se abre muchas veces por día.
    #
    # Meterla igual habría sumado un viaje fijo a cada apertura por un dato que
    # **solo cambia cuando alguien vota**. Cacheado 60s + invalidado en el voto,
    # el costo real es ~0 y el número que ves después de votar es el nuevo.
    medicion = av_agent_evals.precision_por_causa()

    # ⚠️⚠️ **EL ESTADO VIVO DE SALUD, y por qué faltaba.** El user aplicó 5
    # arreglos de FCI y ENCONTRÓ mostró **los mismos 98 hallazgos, número por
    # número**. El arreglo estaba bien; entre el dato y la pantalla hay cuatro
    # capas de foto y ninguna se refrescaba.
    #
    # La regla para esto ya está escrita seis renglones más abajo —*todo
    # criterio que decida si algo se MUESTRA tiene que poder evaluarse en la
    # LECTURA*— y se venía aplicando a `falta_en_base`, `hueco_de_curva`,
    # `sin_flujo` e `ignorados`. **A SALUD no.** Y es el tipo con más filas.
    #
    # `salud.evaluar()` es la ÚNICA función que arma ese estado (no una copia),
    # y cachearla es lo que hace que el cotejo salga gratis: la pantalla se abre
    # muchas veces por día y cada viaje a Supabase cuesta ~8,5 ms de distancia.
    salud_viva = _salud_por_id()
    # Los tickers que HOY tienen ficha. Sale de `av_agent.tickers_con_ficha`, la
    # MISMA función que usa el detector — no una copia de su query.
    con_ficha = _con_ficha()
    verdes = _controles_en_verde()

    simbolos = av_agent.simbolos_primary()
    # **El «no me interesa» también se evalúa AL LEER.** Es la regla de E2.r: un
    # criterio que solo corre al relevar tarda una corrida entera (~29 créditos)
    # en surtir efecto, así que el user aprieta IGNORAR y la fila sigue ahí — que
    # es exactamente lo que hace desconfiar de toda la lista.
    ignorados = av_agent.tickers_ignorados()

    # La ACCIÓN la decide el backend, no el front. Se deriva en la lectura (no se
    # persiste) para que un tipo que se vuelva accionable mañana alcance también a
    # los hallazgos ya guardados.
    for h in filas:
        # ⚠️ **La REGLA gana sobre el TIPO.** `pata_equivocada` y
        # `cotiza_en_pesos` son el mismo tipo y se arreglan distinto: mientras
        # esto miraba solo el tipo, los BOPREALes mostraban un botón que pedía
        # una pata que ya cotizaba y dejaba el master intacto — el hallazgo
        # volvía todas las ruedas y el user votó 17 veces (§0.as).
        h["accion"] = av_agent.accion_de(h.get("tipo") or "", h.get("regla") or "")
        # **NUESTRO o DEL MERCADO** (2026-08-19). Se deriva acá por el mismo motivo
        # que la acción —para que un cambio de criterio alcance a lo ya guardado— y
        # es lo que le permite a la pantalla mostrar por default solo lo que tiene
        # arreglo. Ojo: esconderlo NO es filtrarlo del conteo; los ilíquidos siguen
        # contados y a un clic.
        h["de_quien"] = av_agent.de_quien(h.get("regla") or "")
        # Dónde se anota el voto. Lo decide el backend por el mismo motivo que la
        # acción: si el front lo dedujera del tipo, tendría una segunda tabla de
        # dominios que se separa de ésta sin dar ningún error.
        h["dominio_eval"] = av_agent.dominio_eval(h.get("tipo") or "")
        # ⚠️ **QUÉ SE LE PREGUNTA A ESTA FILA.** No todo hallazgo es un juicio:
        # a un ERROR copiado del log del motor no se le puede preguntar «¿acertó?»
        # —la respuesta es siempre que sí— y esos «siempre sí» llegaban a 10/10 y
        # marcaban la causa como `candidata_a_auto`, o sea abrían la compuerta de
        # autonomía con evidencia que no mide nada. Lo decide el backend, como
        # todo lo demás de la fila.
        h["pregunta"] = av_agent.pregunta_de(h.get("tipo") or "")
        # ── DESDE CUÁNDO Y CUÁNTAS VECES ────────────────────────────────────
        #
        # Lo que convierte «apareció hoy» en «lleva 11 días · 47 ruedas». Un
        # problema crónico y uno de recién se atienden distinto y hasta hoy se
        # veían idénticos.
        #
        # ⚠️ **`volvio` se marca aparte y no se mezcla con la antigüedad.** Un
        # problema que se arregló y REAPARECIÓ es la señal más fuerte que hay
        # —dice que el arreglo no sirvió— y contarlo como uno viejo cualquiera
        # la borra.
        if h.get("abierto_at"):
            h["dias_abierto"] = round(ciclo._dias(h["abierto_at"]), 1)
            # ⚠️ **DESDE CUÁNDO, COMO FECHA Y NO SOLO COMO «hace N días»**
            # (§0.br). El user: *«necesito que haya una columna con la hora y
            # expresar todo en hora argentina»*. `dias_abierto` sirve para
            # ordenar y para el color, pero no ubica el hecho: «4d» no dice si
            # empezó el lunes a la mañana o el jueves a la noche.
            #
            # Se manda el ISO y la pantalla lo escribe en ART. La ALTERNATIVA
            # —mandarlo ya formateado— parece más simple y es peor: el mismo
            # dato no se podría ordenar sin volver a parsear el texto.
            h["abierto_at"] = (h["abierto_at"].isoformat()
                               if hasattr(h["abierto_at"], "isoformat")
                               else str(h["abierto_at"]))
        # ⚠️ **`volvio` se marca aparte y no se mezcla con la antigüedad.** Un
        # problema que se arregló y REAPARECIÓ es la señal más fuerte que hay
        # —dice que el arreglo no sirvió— y contarlo como uno viejo cualquiera
        # la borra.
        if h.get("estado_item") == ciclo.VOLVIO:
            h["volvio"] = True
        # CUÁNTO ACIERTA ESTA CAUSA. `None` = no se pudo medir (distinto de 0
        # votos, que sí es un dato: «nunca nadie juzgó esta regla»).
        if medicion is None:
            h["confianza"] = None
        else:
            nh, okh, tot = medicion.get(h.get("regla") or "", (0, 0, 0))
            h["confianza"] = {
                "humanos": nh, "aciertos": okh, "votos": tot,
                "precision": round(okh / nh, 4) if nh else None,
                # El mismo umbral que el resumen, leído de la misma constante: si
                # la pantalla usara otro, diría «medida» sobre lo que el tablero
                # llama «sin evidencia».
                "suficiente": nh >= av_agent_evals.MIN_VOTOS,
                # ⚠️ **UNA CAUSA PROBADA DEJA DE PREGUNTAR.** `ya_votado` corta
                # la repetición por CASO —el mismo bono con la misma causa— y no
                # alcanza: con `pata_equivocada` en 17/17, un BOPREAL nuevo
                # seguía pidiendo el voto 18. Lo que se mide es si el AGENTE
                # entiende esa CAUSA, y eso ya está contestado: pedir otro voto
                # no agrega evidencia y es la fatiga que el user viene marcando
                # («ya lo completé 40 veces»).
                #
                # Es el mismo umbral que abre la compuerta de autonomía
                # (`candidata_a_auto`), leído de la misma cuenta: si la pantalla
                # usara otro criterio, diría «probada» sobre algo que el tablero
                # todavía llama «sin evidencia».
                #
                # **No es irreversible**: el front deja votar igual, y un ✖ sobre
                # una causa probada la baja del umbral sola en la próxima lectura
                # — que es justo la señal de que el agente empeoró.
                "probada": bool(nh >= av_agent_evals.MIN_VOTOS and okh == nh)}

    def _caduco(h: dict) -> bool:
        if (h.get("ticker") or "").strip().upper() in ignorados:
            return True
        # **Su control quedó en cero → el hallazgo ya no puede ser cierto.**
        # Va PRIMERO y vale para cualquier regla que tenga acción: es el cotejo
        # que evita seguir tapando esto de a un `if` por vez.
        ctl = _control_de_la_regla((h.get("regla") or "").strip())
        if ctl and ctl in verdes:
            return True
        if h["tipo"] == "falta_en_base":
            if h["ticker"] in en_curvas:             # el bono ya está cargado
                return True
            # ⚠️ **Y el filtro de Primary TAMBIÉN se aplica acá.** Ponerlo solo en
            # el detector no alcanzaba: la lista es la foto de la última corrida y
            # relevar cuesta ~29 créditos, así que el filtro no surtía efecto hasta
            # la próxima relevada y los bonos seguían apareciendo igual — la MISMA
            # trampa de TZXM8 y BADLAR. El predicado es el de `av_agent`, no una
            # copia: dos versiones de «¿se descarta?» terminarían contradiciéndose.
            ev = h.get("evidencia") or {}
            return av_agent.descartar_por_primary(
                h["ticker"], bool(ev.get("en_cartera")), simbolos)
        if h["tipo"] == "hueco_de_curva":
            return not curvas_ejes.ajuste_sin_curva((h["ticker"] or "").lower())
        if h["tipo"] == "sin_flujo":
            # **Un `sin_flujo` caduca cuando el bono YA tiene cronograma** — y eso
            # sí se puede leer barato, al revés de lo que decía este comentario
            # hasta el 2026-08-17 («no hay forma barata de saber si se arreglaron
            # sin rehacer la corrida»). Era falso: el cronograma vive en el mismo
            # `mercado.curvas` que ya se está leyendo.
            #
            # El síntoma: el user completó DICP, el libro de acciones lo registró,
            # la fila decía «✔ CRONOGRAMA ESCRITO» **y el hallazgo seguía en la
            # lista**. Tercera vez que aparece el mismo patrón (TZXM8, BADLAR,
            # IGNORAR) y por eso la regla ya está escrita en el doc: *todo criterio
            # que decida si algo se MUESTRA tiene que poder evaluarse en la
            # LECTURA*. Acá el criterio existía y no se había aplicado.
            #
            # El predicado es `tiene_flujo_def`, **el mismo que lo DETECTA** — si
            # fuera un `bool(flujos)` propio, una LECAP zero-coupon caducaría por
            # un motivo distinto del que la marcó.
            from api.services.acreencias import tiene_flujo_def
            doc = docs_curvas.get((h.get("ticker") or "").strip().upper())
            return bool(doc) and tiene_flujo_def(doc, date.today())
        if h["tipo"] == "salud":
            # **Un chequeo de SALUD caduca cuando vuelve a estar en verde.** El
            # `ticker` del hallazgo transporta el id del chequeo
            # (`control:patas_equivocadas`, `job:cierre_canje`) — así lo emite
            # `detectar_salud`.
            #
            # `None` = el chequeo ya NO EXISTE en la evaluación de hoy (se sacó
            # del catálogo, o no se pudo evaluar). Eso **no** se trata como
            # resuelto: «no lo encontré» y «está bien» no son lo mismo, y
            # confundirlos vaciaría la pantalla justo el día que SALUD falla.
            vivo = salud_viva.get((h.get("ticker") or "").strip())
            return bool(vivo) and (vivo.get("estado") or "") == "ok"
        if h.get("regla") == "sin_espejo_en_assets":
            # ⚠️⚠️ **NO TODO `tasa_sospechosa` HABLA DE LA TASA.** Este tipo
            # quedaba entero sin cotejo con el argumento de abajo —una tasa
            # depende del precio del día y no se puede reverificar barato— y es
            # cierto para sus reglas de tasa. Pero `sin_espejo_en_assets` no es
            # una tasa: es *«¿existe este ticker en `portafolio.assets`?»*, un
            # hecho de base que se contesta con UNA query.
            #
            # El costo se vio en vivo (2026-08-22): el user corrigió los dos
            # tickers, el control `assets_ticker_partido` se puso en **0**… y
            # PLC5O y S13N6 seguían en ENCONTRÓ. Dos partes del sistema
            # afirmando lo contrario en la misma pantalla.
            #
            # Es el error de siempre: usar el TIPO como proxy de «¿esto se puede
            # reverificar?» cuando lo que lo decide es la REGLA.
            #
            # `None` = no pude leer assets → NO caduca. «No sé» nunca es
            # «se arregló».
            return (con_ficha is not None
                    and (h.get("ticker") or "").strip().upper() in con_ficha)
        # ── Los TRES cotejos que §0.cg dejó NOMBRADOS como deuda ──────────
        # («si se arreglan hoy, van a quedarse en pantalla igual») — y pasó
        # textual: el user aplicó los arreglos, la cadena decía «ya no aparece:
        # se resolvió solo» y las 11 filas seguían en ENCONTRÓ (2026-08-22).
        # Son hechos de BASE: se cotejan con lo que esta misma query ya trajo.
        if h.get("regla") == "pata_equivocada":
            # Resuelto ⟺ el master YA apunta al símbolo sugerido (la pata de la
            # moneda del eje). Se compara contra la COLUMNA `instrumento` — la
            # que gana (§0.y) — y con el símbolo COMPLETO, no el ticker corto.
            # Sin `sugerido` en la evidencia (hallazgo viejo) no se caduca:
            # «no sé» nunca es «se arregló».
            sug = ((h.get("evidencia") or {}).get("sugerido") or "").strip()
            actual = simbolo_master.get((h.get("ticker") or "").strip().upper())
            return bool(sug) and bool(actual) and actual == sug
        if h.get("regla") == "sin_ejes":
            # Resuelto ⟺ el doc YA cae en una curva. El predicado es el del
            # detector (`curvas_ejes.ejes_de_doc`), no una copia.
            doc = docs_curvas.get((h.get("ticker") or "").strip().upper())
            return bool(doc) and curvas_ejes.ejes_de_doc(doc) is not None
        if h.get("regla") == "moneda_flujo_contradice":
            # Resuelto ⟺ `moneda_flujo` ya coincide con lo que los ejes esperan.
            # MISMAS funciones que el detector. Si `esperada` no se puede
            # calcular, NO caduca: la contradicción no se pudo evaluar.
            doc = docs_curvas.get((h.get("ticker") or "").strip().upper())
            if not doc:
                return False
            from engines.curvas import moneda_flujo_esperada
            esperada = moneda_flujo_esperada(doc)
            actual = (doc.get("moneda_flujo") or "").strip().upper()
            return bool(esperada) and actual == esperada
        # El resto de `tasa_sospechosa` sí habla de la TASA, que depende del
        # precio del día: no se puede afirmar que se arregló sin volver a
        # cotejar contra 1816.
        return False

    filas = [h for h in filas if not _caduco(h)]
    for h in filas:
        _refrescar_salud(h, salud_viva)
    filas.sort(key=lambda h: (_ORDEN_SEV.get(h["severidad"], 9), h["ticker"]))
    return filas, corrida.isoformat()


def _refrescar_salud(h: dict, salud_viva: dict[str, dict]) -> None:
    """Reescribe la fila de un chequeo con lo que dice SALUD **ahora**.

    ⚠️⚠️ **POR QUÉ ESTO ES UNA FEATURE Y NO UN DETALLE.** Tacharla cuando vuelve
    al verde ya estaba (`_caduco`). Lo que faltaba es el caso normal: **arreglar
    una parte**. El user arregló 5 de los 8 FCI incompletos y ENCONTRÓ siguió
    diciendo «8 anomalías sin resolver», porque ese texto es de la foto de
    anoche. Trabajo real hecho, cero señal en la pantalla — y después de tres
    veces seguidas, la conclusión razonable es que el agente no arregla nada.

    Se refresca **el texto y el conteo**, no la existencia de la fila: el
    control sigue rojo y tiene que seguir a la vista. Y se guarda `n_casos_foto`
    para poder decir **«3 casos · eran 8»**, que es la única forma de que el
    avance se lea de un vistazo sin tener que acordarse del número de ayer.

    `n` lo publica `salud._chequeos_controles` (`len(activos)`) y el motivo lo
    arma `av_agent._motivo_salud` — las dos son las funciones ORIGINALES, no
    copias: si mañana cambia cómo se cuenta o cómo se redacta, esto lo hereda.

    Silencioso a propósito cuando el chequeo no está vivo: la fila se queda tal
    cual vino de la foto. «No lo pude evaluar» nunca puede parecer «se arregló».
    """
    if h.get("tipo") != "salud":
        return
    vivo = salud_viva.get((h.get("ticker") or "").strip())
    if not vivo:
        return
    try:
        h["motivo"] = av_agent._motivo_salud(vivo)
    except Exception as e:                       # pragma: no cover - defensivo
        logger.warning("vista: no pude rearmar el motivo de %s (%s)",
                       h.get("ticker"), e)
    viejo_n = (h.get("evidencia") or {}).get("n_casos")
    n = vivo.get("n")
    if isinstance(n, int):
        h["n_casos"] = n
        # El número de la FOTO, para poder mostrar el delta. Si la foto no lo
        # traía (hallazgos viejos, de antes de este campo) no se inventa: sin
        # `n_casos_foto` el front muestra el número a secas y listo.
        if isinstance(viejo_n, int) and viejo_n != n:
            h["n_casos_foto"] = viejo_n
    h["severidad"] = {"error": "alta", "warn": "media"}.get(
        (vivo.get("estado") or "").lower(), h.get("severidad") or "media")


def _ignorados() -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, motivo, por, creado_at "
                    "FROM agente.av_agent_ignorados ORDER BY creado_at DESC")
        return [{"ticker": r[0], "motivo": r[1], "por": r[2],
                 "creado_at": r[3].isoformat() if r[3] else None}
                for r in cur.fetchall()]


def _decididas(limite: int = 40) -> list[dict]:
    """Lo ya contestado, lo más reciente primero.

    Se muestra en la vista a propósito: sin el historial, el agente parece
    empezar de cero cada vez y no hay forma de ver qué criterio se viene
    aplicando — ni de arrepentirse de una decisión."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, clave, tipo, pregunta, respuesta, nota, respondida_por, "
            "respondida_at, aplicada_at FROM agente.av_agent_preguntas "
            "WHERE estado = 'respondida' ORDER BY respondida_at DESC NULLS LAST "
            "LIMIT %s", (limite,))
        cols = ["id", "clave", "tipo", "pregunta", "respuesta", "nota",
                "respondida_por", "respondida_at", "aplicada_at"]
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r, strict=False))
            for k in ("respondida_at", "aplicada_at"):
                d[k] = d[k].isoformat() if d[k] else None
            out.append(d)
        return out


# ⚠️ Acá vivían `_aplicados_ok()` y `_hecho()`: consultaban
# `av_agent_propuestas` para deducir si un hallazgo ya se había atendido. Eran
# **una de las cinco derivaciones ad-hoc** que se contradecían entre sí (§0.bc).
# Ahora el estado lo tiene el objeto y viene en el mismo JOIN — una query menos
# y una fuente de verdad menos.


def _seguimiento_corto(limite: int = 12) -> dict:
    """Cuántos arreglos están en prueba, cuántos aguantaron y cuáles volvieron.

    Lo que está en prueba va topeado: una lista de 200 «todavía no volvió» no
    informa más que su número.

    ⚠️ **AGRUPADO POR CAUSA** (user, 2026-08-22: *«¿AGUANTAN? 199?? no tiene
    lógica»*). Un lote que arregló 133 patas mete 133 seguimientos idénticos:
    mismo arreglo, mismo día, mismo reloj. Mostrarlos uno por uno es ruido que
    tapa a los distintos — la fila útil es *«pata_equivocada ×133 · día 2 ·
    próximo control al día 3»*. El detalle individual no se pierde: se agrupa
    en la lectura, y `en_seguimiento()` sigue devolviendo todo.
    """
    from api.services import av_agent_items
    try:
        filas = av_agent_items.en_seguimiento()
    except Exception as e:
        logger.warning("av_agent: no pude leer el seguimiento (%s)", e)
        return {}
    en_prueba = [x for x in filas if not x["aguanto"]]
    grupos: dict[str, list[dict]] = {}
    for x in en_prueba:
        grupos.setdefault(x.get("regla") or "?", []).append(x)
    por_causa = []
    for regla, xs in grupos.items():
        xs.sort(key=lambda x: -x["dias"])
        viejo = xs[0]
        por_causa.append({
            "regla": regla, "n": len(xs),
            # El rango de días: un grupo puede tener arreglos de distintas
            # fechas y «día 2» a secas mentiría sobre la mitad.
            "dias_min": xs[-1]["dias"], "dias_max": viejo["dias"],
            "hitos": viejo["hitos"], "de": viejo["de"],
            "proximo_hito_en_dias": min(
                (x["proximo_hito_en_dias"] for x in xs
                 if x["proximo_hito_en_dias"] is not None), default=None),
            # Con pocos casos, los nombres dicen más que el número.
            "sujetos": [x["sujeto"] for x in xs[:5]],
        })
    por_causa.sort(key=lambda g: -g["n"])
    return {
        "en_prueba": len(en_prueba),
        "aguantaron": sum(1 for x in filas if x["aguanto"]),
        "por_causa": por_causa[:limite],
        # Los que llevan MÁS tiempo primero: son los que van a dar novedad antes.
        "proximos": sorted(en_prueba, key=lambda x: -x["dias"])[:limite],
    }


def _que_importa_corto(limite: int = 25) -> dict:
    """Las bandas + los GRUPOS por causa + las primeras filas. **Nunca
    levanta**: si la memoria no se puede leer, la pantalla se dibuja igual.

    ⚠️ **EL RESUMEN ES POR CAUSA, no fila por fila** (user, 2026-08-22: *«QUÉ
    PIDE ALGO es la peor de todas: un número altísimo, no se puede hacer nada
    y encima figuran unas pares nada más»*). 54 filas donde 30 son la misma
    causa no son 54 decisiones: son un puñado de causas con su tamaño. El
    grupo además es el PUENTE al banco de trabajo — la pantalla lo hace
    clickeable y te deja en LA LISTA filtrada por esa causa, que es donde
    están los botones. Y lo truncado SE DICE (`filas` va topeado; el total
    viaja en `piden_algo`/`abiertos`): mostrar 25 de 54 sin decirlo es
    truncar en silencio.
    """
    try:
        from api.services import av_agent_items
        r = av_agent_items.que_importa()
    except Exception as e:
        logger.warning("av_agent: no pude leer qué importa (%s)", e)
        return {"ok": False, "abiertos": 0, "piden_algo": 0,
                "por_banda": {}, "filas": [], "sin_mirar": [], "por_causa": []}
    grupos: dict[str, dict] = {}
    orden_banda = {b: i for i, b in enumerate(ciclo.BANDAS)}
    for f in r.get("filas") or []:
        g = grupos.setdefault(f.get("regla") or "?", {
            "regla": f.get("regla") or "?", "tipo": f.get("tipo") or "",
            "n": 0, "piden": 0, "peor_banda": "nuevo", "dias_max": 0.0,
            "sujetos": []})
        g["n"] += 1
        if f.get("banda") in ("volvio", "estancado", "arrastra"):
            g["piden"] += 1
        if orden_banda.get(f.get("banda"), 9) < orden_banda.get(g["peor_banda"], 9):
            g["peor_banda"] = f.get("banda")
        g["dias_max"] = max(g["dias_max"], float(f.get("dias_abierto") or 0))
        if len(g["sujetos"]) < 4:
            g["sujetos"].append(f.get("sujeto") or "")
    por_causa = sorted(grupos.values(),
                       key=lambda g: (-g["piden"], -g["n"]))
    return {**r, "filas": r.get("filas", [])[:limite],
            "por_causa": por_causa[:40],
            "sin_mirar": r.get("sin_mirar", [])[:10]}


def vista() -> dict:
    """Todo lo que la pantalla necesita, en un request."""
    from api.services import av_agent_acciones as acciones
    from api.services import av_agent_preguntas as preg

    hallazgos, corrida_at = _hallazgos_ultima_corrida()
    abiertas = preg.abiertas()

    # ⚠️ **LO YA VOTADO NO SE VUELVE A PREGUNTAR** (user, 2026-08-20: *«otra vez
    # lo mismo, ya lo completé 40 veces»*). Los BOPREALes llegaron a 17/17: el
    # mismo bono con la misma causa, con los botones ¿ACERTÓ? intactos en cada
    # rueda. El voto mide al AGENTE, no al día — repetirlo no agrega un dato y
    # convierte la pantalla en un formulario que hay que llenar de nuevo todas
    # las mañanas. UNA query para toda la lista.
    votados = av_agent_evals.ya_votados()
    # Lo que una persona marcó «✖ es ruido». Una query para toda la lista.
    ruido = av_agent_evals.es_ruido()

    por_tipo: dict[str, int] = {}
    por_regla: dict[str, int] = {}
    for h in hallazgos:
        por_tipo[h["tipo"]] = por_tipo.get(h["tipo"], 0) + 1
        por_regla[h["regla"]] = por_regla.get(h["regla"], 0) + 1
        # El par que se vota es (SUJETO, CAUSA) — el mismo que usa `votar`. Si el
        # agente cambia de causa para ese bono, es un par nuevo y sí se pregunta.
        # ⚠️ El sujeto va por `clave_caso` — LA normalización de evals. Acá se
        # buscaba con `.strip()` pelado mientras `votar` guardaba `.upper()`:
        # los bonos matcheaban de casualidad (ya vienen en mayúscula) y los
        # votos sobre motores/tablas (`manager.salud_eventos`) no se
        # recordaban NUNCA — el «me voy de tab, vuelvo, y están los botones
        # otra vez» que el user reportó tres veces.
        voto = votados.get((av_agent_evals.clave_caso(h.get("ticker")),
                            (h.get("regla") or "").strip()))
        if voto is not None:
            h["ya_votado"] = True
            h["voto"] = voto
        # ⚠️ **POR QUÉ NO HAY BOTÓN** (§0.ap). Una fila sin acción y sin
        # explicación se lee como que el agente no sabe qué hacer con lo que él
        # mismo encontró. Se DERIVA en la lectura: el día que un tipo consiga su
        # puerta, los hallazgos ya guardados la heredan solos.
        pu = av_agent.puerta(h.get("tipo") or "")
        if not pu["hay"]:
            h["sin_puerta"] = pu

        # ── ¿ESTO YA LO ATENDÍ? ──────────────────────────────────────────────
        #
        # Pedido del user (2026-08-21): *«que ENCONTRÓ muestre por defecto lo que
        # NO hice; que estos queden en ENCONTRÓ pero marcados como ya hechos»*.
        # Con 107 filas de las cuales la mayoría ya pasaron por sus manos, la
        # lista de trabajo dejó de ser una lista de trabajo.
        #
        # **ATENDIDO = ya pasó por tus manos.** Aplicaste su arreglo, o lo
        # votaste. Las dos cosas cuentan, y la MARCA dice cuál fue.
        #
        # ⚠️ **La primera versión NO contaba el voto cuando la fila tenía botón**,
        # con este argumento: votar no arregla nada, así que esconder un bono
        # votado y roto sería peor. El razonamiento es correcto sobre el DATO y
        # equivocado sobre la PANTALLA, y el user lo dijo dos veces: *«que
        # ENCONTRÓ muestre por defecto lo que NO hice… si no es imposible
        # avanzar»*. Si votó, lo hizo. Que además falte apretar el arreglo se
        # dice con la marca (`votado` ≠ `aplicado`), no dejándolo arriba de todo
        # como si no lo hubiera mirado nunca.
        #
        # Lo que impide esconder algo roto no es este filtro: la fila **sigue en
        # la lista**, contada arriba y a un clic. Esconder con el número a la
        # vista no es truncar; dejar 107 filas donde 90 ya se miraron, sí es
        # perder la lista de trabajo.
        # ⚠️ **AHORA SALE DEL OBJETO.** Antes esto consultaba
        # `av_agent_propuestas` por su cuenta (`_aplicados_ok`): era **una de
        # las cinco derivaciones ad-hoc** que se contradecían entre sí y que
        # motivaron toda la migración (§0.bc). El estado del hallazgo lo tiene
        # el hallazgo — la pantalla lo lee, no lo deduce.
        #
        #   en_curso  → apretaste el arreglo y falta que el detector confirme
        #   resuelto  → el detector volvió a mirar y ya no está
        #   votado    → no había botón (o no lo apretaste) pero lo juzgaste
        # ── EL NOMBRE PARA LA PANTALLA (§0.bq) ──────────────────────────────
        #
        # ⚠️ Para un bono el sujeto ES el nombre (`AL30`). Para un chequeo es un
        # ID —`control:comitentes_sin_nivel1`— que en una columna angosta se
        # corta y deja la fila sin decir qué es. **El nombre legible ya venía
        # en la evidencia** (`titulo`) y nadie lo leía.
        #
        # Se resuelve ACÁ y no en el navegador para que las dos pantallas que
        # muestran hallazgos digan lo mismo.
        h["nombre"] = str((h.get("evidencia") or {}).get("titulo")
                          or h.get("ticker") or "")
        h["atendido"] = ""
        est = h.pop("estado_item", None)
        if est in (ciclo.EN_CURSO, ciclo.RESUELTO):
            h["atendido"] = "aplicado"
        elif est == ciclo.VISTO or h.get("ya_votado"):
            h["atendido"] = "votado"
        # ⚠️ **«ES RUIDO» AHORA HACE ALGO** (§0.bn). Esos votos se escribían y
        # no los leía nadie: la fila quedaba exactamente donde estaba, que es
        # la peor versión posible de un botón porque parece que hizo algo.
        if (av_agent_evals.clave_caso(h.get("ticker")),
                (h.get("regla") or "").strip()) in ruido:
            h["es_ruido"] = True

    # Se marca y NO se filtra acá: el corte lo hace la pantalla, que ya sabe
    # esconder y contar lo escondido. Sacarlo del payload lo volvería
    # irrecuperable desde la app — y esconder sin poder volver atrás es cómo se
    # consigue que nadie marque nada.
    n_ruido = sum(1 for h in hallazgos if h.get("es_ruido"))

    return {
        "corrida_at": corrida_at,
        # Lo contestado que TODAVÍA no surtió efecto. Sin esto, el user contesta
        # 12 altas y no tiene dónde mirar qué pasó con ellas — una decisión que no
        # se ve en ningún lado se siente como una decisión perdida.
        "pendientes": preg.pendientes_de_aplicar(),
        # El LIBRO: qué escribió el agente, cuándo y en qué tabla. Una escritura
        # automática sin libro es una escritura que nadie puede auditar.
        "acciones": acciones.listar(),
        # AVISOS: lo que el agente dejó listo salvo un dato que solo puede poner
        # una persona. Derivado en vivo → se cierra solo al cargar el número.
        "avisos": avisos(),
        # Lo que el agente MANDÓ, aparte de lo que hay para completar acá.
        "mensajes": mensajes(),
        "hallazgos": hallazgos,
        "por_tipo": por_tipo,
        "por_regla": por_regla,
        # Cuánto de la lista ya pasó por tus manos. Va SIEMPRE, aunque esté
        # escondido: un filtro que oculta sin decir cuánto oculta es lo mismo que
        # truncar en silencio.
        "atendidos": sum(1 for h in hallazgos if h.get("atendido")),
        # Cuántas dijiste que no querías ver. Va SIEMPRE, aunque estén
        # escondidas: un filtro que oculta sin decir cuánto oculta es lo mismo
        # que truncar en silencio.
        "es_ruido": n_ruido,
        # **CUÁNTO DE LO QUE VE, PUEDE RESOLVER** — y qué pared conviene romper
        # primero, ordenada por cuántas veces aparece. Es la medición que le
        # faltaba al agente sobre sí mismo: sin ella, la única forma de saber
        # cuál era la peor era que alguien se hartara de verla (§0.ap).
        "cobertura": av_agent.cobertura(hallazgos),
        # ── LOS ARREGLOS CON EL RELOJ CORRIENDO (§0.bi) ─────────────────────
        #
        # Lo único que el agente sabe de sí mismo **sin que se lo diga nadie**:
        # de los problemas que dio por resueltos, cuántos aguantaron y cuántos
        # volvieron. Un ✔ tuyo es una opinión; que algo no haya vuelto en 30
        # días no lo es.
        #
        # Va en la vista porque **una medición que no se ve no existe** (§0.l):
        # el escalonado estuvo construido sin que nadie lo llamara, que es la
        # misma enfermedad de siempre.
        "seguimiento": _seguimiento_corto(),
        # ── QUÉ PIDE ALGO HOY (§0.bm) ──────────────────────────────────────
        #
        # La historia se guardaba desde §0.bd y la pantalla seguía ordenando
        # por severidad — o sea igual que ANTES de tener memoria. Esto la lee:
        # bandas (volvió · estancado · arrastra · nuevo) y el número que
        # convierte una lista en una decisión: *de N abiertos, M piden algo*.
        "que_importa": _que_importa_corto(),
        "preguntas": [p for p in abiertas if p["tipo"] != "decision"],
        "decisiones": [p for p in abiertas if p["tipo"] == "decision"],
        "decididas": _decididas(),
        # Lo VOTADO, para el HISTORIAL: «voy tachando cosas y nada pasa a
        # historial» (user, 2026-08-22). El voto apaga la fila en ENCONTRÓ y
        # sin esto el rastro de qué contestaste no vivía en ninguna pantalla.
        "votos": av_agent_evals.ultimos(),
        "ignorados": _ignorados(),
        # Lo que el agente TODAVÍA no sabe hacer, dicho por él mismo. Comunicar
        # las capacidades es parte del contrato con el usuario: sin esto, un
        # "alta" que queda guardado sin aplicarse se lee como que el botón no
        # anduvo.
        "capacidades": {
            "puede_ignorar": True,
            # E2 ya existe: SIMULAR baja el cuadro de 1816, calcula la TEA en seco
            # y corre el pre-flight; APLICAR escribe por `upsert_bono`. Decía
            # `False` con el motivo "es la etapa E2" cuando E2 ya estaba hecha —
            # una capacidad mal declarada hace que el botón se lea como roto,
            # justo el problema que este campo venía a evitar.
            "puede_dar_de_alta": True,
            "motivo_alta": ("SIMULAR no escribe nada: baja el cuadro de 1816, "
                            "calcula la TEA que tendría y valida la cadena entera "
                            "(especie, símbolo en Primary, suscripción, snapshot). "
                            "APLICAR solo se ofrece si esa cadena no tiene ningún "
                            "paso bloqueado. Las ramas sin conversión inequívoca "
                            "(tamar, dólar-linked, ajustes sin fórmula) se simulan "
                            "igual, pero las carga un humano."),
        },
    }
