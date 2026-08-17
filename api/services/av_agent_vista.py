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

from api.services import av_agent
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
                       c.data->>'cer_emision', c.emisor, (c.ticker IS NOT NULL)
                  FROM mercado.av_agent_avisos a
                  LEFT JOIN mercado.curvas c ON c.ticker = a.ticker
                 {'' if incluir_resueltos else 'WHERE NOT a.resuelto'}
                 ORDER BY a.resuelto, a.creado_at DESC
            """)
            filas = cur.fetchall()
    except Exception as e:
        logger.warning("av_agent: no se pudieron leer los avisos: %s", e)
        return []

    out: list[dict] = []
    for (aid, ticker, clave, que, porque, donde, creado, resuelto,
         rpor, rat, cer, emisor, en_curvas) in filas:
        cond = _COND_AVISO.get(clave)
        ya = bool(cond({"cer_emision": cer, "emisor": emisor})) if (cond and en_curvas) \
            else None
        out.append({
            "id": aid, "ticker": ticker, "clave": clave, "que_hacer": que,
            "por_que": porque, "donde": donde,
            "creado_at": creado.isoformat() if creado else None,
            "resuelto": resuelto, "resuelto_por": rpor,
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
        cur.execute("SELECT max(corrida_at) FROM mercado.av_agent_hallazgos")
        fila = cur.fetchone()
        corrida = fila[0] if fila else None
        if not corrida:
            return [], None
        cur.execute(
            f"SELECT {', '.join(_COLS_H)} FROM mercado.av_agent_hallazgos "
            "WHERE corrida_at = %s", (corrida,))
        filas = [dict(zip(_COLS_H, r, strict=False)) for r in cur.fetchall()]
        cur.execute("SELECT ticker FROM mercado.curvas")
        en_curvas = {r[0] for r in cur.fetchall()}

    # ⚠️ **Cada tipo caduca por su PROPIA razón, y el campo `ticker` no significa
    # lo mismo en todos.** Acá se aplicaba UN predicado a dos tipos distintos: en
    # un `hueco_de_curva` el `ticker` es el **AJUSTE** (`BADLAR`), no un bono, así
    # que compararlo contra `mercado.curvas.ticker` no lo sacaba nunca — el user
    # lo vio con BADLAR, cuya curva el agente YA había creado.
    #
    # `ajuste_sin_curva` es la MISMA función que lo detecta y ya lee el catálogo,
    # así que preguntarle de nuevo no puede dar un criterio distinto.
    # El universo de Primary, cacheado 600s → pedirlo por lectura es gratis.
    simbolos = av_agent.simbolos_primary()

    def _caduco(h: dict) -> bool:
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
        # `sin_flujo` y `tasa_sospechosa` hablan de un bono que YA está en el
        # master: existir no los resuelve, y no hay forma barata de saber si se
        # arreglaron sin rehacer la corrida.
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

    por_tipo: dict[str, int] = {}
    por_regla: dict[str, int] = {}
    for h in hallazgos:
        por_tipo[h["tipo"]] = por_tipo.get(h["tipo"], 0) + 1
        por_regla[h["regla"]] = por_regla.get(h["regla"], 0) + 1

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
        "hallazgos": hallazgos,
        "por_tipo": por_tipo,
        "por_regla": por_regla,
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
