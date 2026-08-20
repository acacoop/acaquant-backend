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

from api.services import av_agent, av_agent_evals
from core import curvas_ejes
from core.postgres import get_pool

logger = logging.getLogger(__name__)

_COLS_H = ["tipo", "ticker", "regla", "severidad", "motivo", "evidencia"]
_ORDEN_SEV = {"alta": 0, "media": 1, "baja": 2}


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
                "INSERT INTO mercado.av_agent_avisos "
                "(ticker, clave, que_hacer, por_que, donde, creado_por) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING", filas)
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
                "UPDATE mercado.av_agent_avisos SET resuelto = %s, "
                "resuelto_por = %s, resuelto_at = CASE WHEN %s THEN NULL ELSE now() END "
                "WHERE id = %s RETURNING ticker, clave",
                (not deshacer, (por or None) if not deshacer else None,
                 deshacer, aviso_id))
            fila = cur.fetchone()
        if not fila:
            return {"ok": False, "error": "no existe ese aviso"}
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
            cur.execute("SELECT ticker, clave, resuelto FROM mercado.av_agent_avisos "
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
            cur.execute("UPDATE mercado.av_agent_avisos SET resuelto = true, "
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
                  FROM mercado.av_agent_avisos a
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
            "INSERT INTO mercado.av_agent_avisos "
            "(ticker, clave, que_hacer, por_que, donde, creado_por, para) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (ticker, clave, que_hacer[:500], (por_que or "")[:300],
             donde or None, por or None, email))
        return cur.rowcount


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
                "FROM mercado.av_agent_avisos a "
                "LEFT JOIN mercado.av_agent_aviso_items i ON i.aviso_id = a.id "
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
                "FROM mercado.av_agent_avisos "
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
                    "FROM mercado.av_agent_aviso_items "
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
                "UPDATE mercado.av_agent_avisos SET resuelto = true, "
                "resuelto_por = %s, resuelto_at = now() "
                "WHERE id = %s AND lower(para) = %s AND NOT resuelto "
                "RETURNING ticker, clave", (e, aviso_id, e))
            fila = cur.fetchone()
    except Exception as ex:
        logger.warning("av_agent: no se pudo cerrar el aviso %s: %s", aviso_id, ex)
        return {"ok": False, "error": str(ex)[:200]}
    if not fila:
        return {"ok": False, "error": "no existe ese aviso"}
    return {"ok": True, "ticker": fila[0], "clave": fila[1], "resuelto": True}


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
        cur.execute("SELECT max(corrida_at) FROM mercado.av_agent_hallazgos "
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
            f"SELECT {', '.join(_COLS_H)} FROM mercado.av_agent_hallazgos "
            "WHERE (%s IS NOT NULL AND corrida_at = %s AND alcance <> ALL(%s)) "
            "   OR (alcance = ANY(%s) AND ("
            #     una buena noticia: dura poco
            "         (regla = ANY(%s) AND corrida_at > now() - "
            "             make_interval(secs => %s))"
            #     una observación de mercado: solo vale si es reciente
            "      OR (regla = ANY(%s) AND corrida_at > now() - "
            "             make_interval(secs => %s))"
            #     un problema nuestro: sigue siendo cierto con el mercado cerrado
            "      OR (regla <> ALL(%s) AND regla <> ALL(%s))))",
            (corrida, corrida, vivos, vivos,
             rapido, av_agent.VENCE_RAPIDO_S,
             obs, av_agent.VENCE_OBSERVACION_S, obs, rapido))
        filas = [dict(zip(_COLS_H, r, strict=False)) for r in cur.fetchall()]
        # El blob completo, no solo la PK: `sin_flujo` caduca cuando el bono YA
        # tiene cronograma, y eso se lee acá mismo. **Es la misma query** — el
        # peaje de Supabase se paga por viaje, no por columna.
        cur.execute("SELECT ticker, data FROM mercado.curvas")
        docs_curvas = {(r[0] or "").strip().upper(): (r[1] or {})
                       for r in cur.fetchall()}
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
        h["accion"] = av_agent.ACCION_POR_TIPO.get(h.get("tipo") or "")
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
                "suficiente": nh >= av_agent_evals.MIN_VOTOS}

    def _caduco(h: dict) -> bool:
        if (h.get("ticker") or "").strip().upper() in ignorados:
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
        # `tasa_sospechosa` habla de la TASA, que depende del precio del día: no
        # se puede afirmar que se arregló sin volver a cotejar contra 1816.
        return False

    filas = [h for h in filas if not _caduco(h)]
    filas.sort(key=lambda h: (_ORDEN_SEV.get(h["severidad"], 9), h["ticker"]))
    return filas, corrida.isoformat()


def _ignorados() -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, motivo, por, creado_at "
                    "FROM mercado.av_agent_ignorados ORDER BY creado_at DESC")
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
            "respondida_at, aplicada_at FROM mercado.av_agent_preguntas "
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

    por_tipo: dict[str, int] = {}
    por_regla: dict[str, int] = {}
    for h in hallazgos:
        por_tipo[h["tipo"]] = por_tipo.get(h["tipo"], 0) + 1
        por_regla[h["regla"]] = por_regla.get(h["regla"], 0) + 1
        # El par que se vota es (SUJETO, CAUSA) — el mismo que usa `votar`. Si el
        # agente cambia de causa para ese bono, es un par nuevo y sí se pregunta.
        voto = votados.get(((h.get("ticker") or "").strip(),
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
        # **CUÁNTO DE LO QUE VE, PUEDE RESOLVER** — y qué pared conviene romper
        # primero, ordenada por cuántas veces aparece. Es la medición que le
        # faltaba al agente sobre sí mismo: sin ella, la única forma de saber
        # cuál era la peor era que alguien se hartara de verla (§0.ap).
        "cobertura": av_agent.cobertura(hallazgos),
        "preguntas": [p for p in abiertas if p["tipo"] != "decision"],
        "decisiones": [p for p in abiertas if p["tipo"] == "decision"],
        "decididas": _decididas(),
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
