"""copiloto/research.py — vista RESEARCH unificada (decisión user 2026-07-20):
UN copiloto que ve TODO /research a la vez — el research "sabe de todo" y su
valor es el CRUCE entre fuentes. Doc madre: docs/VISTA_RESEARCH.md (§ IA
Nivel 2) + docs/COPILOTO.md.

La tabla TSV concatena las 4 tabs SIEMPRE (columna `fuente` distingue):
  - 1816      → watch de bonos: último TEA/paridad/precio/duration + cambios
                de TEA 7d/30d (en pp, precalculados).
  - bcra      → variables BCRA del watch: último valor + cambios 7d/30d.
  - fred      → series FRED del watch: ídem.
  - reportes  → documentos cargados (título, fecha, tipo, comentario).
`params.tab` NO filtra: solo marca dónde está parado el usuario (prioridad si
la pregunta es ambigua). rv-int tiene su propio copiloto, vista `reuters`.
Los watch son curados (decenas de filas por fuente, caps defensivos) — el total
es comparable a la tabla de renta_variable (~187 filas), probada en producción.

Extras SIEMPRE: el mail de research más reciente ("el día en pocas líneas") +
búsqueda full-text determinista sobre los mails con los términos de la PREGUNTA
(el modelo solo puede citar fragmentos que el código trajo, cada uno con fecha)
+ CCL live. TEA/paridad de 1816 vienen en FRACCIÓN → acá se pasan a % (cero
aritmética del modelo). Los readers EOD van con @cached (kwargs-only al
invocar) — N preguntas comparten las mismas queries.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

from api.cache import cached

from .base import _num

logger = logging.getLogger(__name__)

_TABS = ("argentina", "bcra", "internacional", "reportes")
_MAX_FILAS = 120          # techo defensivo de filas por FUENTE (los watch son curados)
_MAX_REPORTES = 30        # documentos: los más recientes
_VENTANA_1816_DIAS = 45   # historia leída para derivar cambios 7d/30d (diario)
_VENTANA_FRED_DIAS = 120  # FRED tiene series semanales/mensuales → ventana más larga
_FTS_TERMINOS = 3         # términos de la pregunta que se buscan en los mails
_FTS_FRAGMENTOS = 4       # fragmentos máximos inyectados
_MAIL_HOY_CHARS = 2200    # recorte del mail más reciente

# Palabras de la pregunta que NO sirven para buscar en los mails.
_STOPWORDS = {
    "1816", "research", "dijo", "dice", "dicen", "diciendo", "viene", "vienen",
    "sobre", "acerca", "respecto", "para", "como", "cómo", "cuál", "cual",
    "cuales", "cuáles", "qué", "que", "quién", "quien", "dónde", "donde",
    "cuándo", "cuando", "está", "esta", "están", "estan", "hay", "las", "los",
    "una", "uno", "unos", "unas", "del", "con", "por", "más", "mas", "menos",
    "hoy", "ayer", "semana", "mes", "año", "resumen", "resumime", "contame",
    "explicame", "leeme", "mails", "mail", "tema", "temas", "ultimo", "último",
    "ultimos", "últimos", "puntos", "importa", "importan", "mesa",
}


def _sanear_params_research(params: dict | None) -> str:
    p = params if isinstance(params, dict) else {}
    tab = str(p.get("tab") or "").strip().lower()
    return tab if tab in _TABS else "argentina"


def _ultimo_y_cambios(puntos: list[tuple], escala: float = 1.0) -> dict:
    """(fecha, valor) ASC → {ultimo, fecha_ultimo, cambio_7d, cambio_30d}.
    Cambio = diferencia ABSOLUTA contra el punto más reciente a ≥7/≥30 días del
    último (semántica uniforme para tasas, niveles y precios). PURA (testeable)."""
    limpio = [(f, v) for f, v in puntos if v is not None]
    if not limpio:
        return {"ultimo": None, "fecha_ultimo": None, "cambio_7d": None, "cambio_30d": None}
    f_ult, v_ult = limpio[-1]

    def _hace(dias: int):
        objetivo = f_ult - timedelta(days=dias)
        prev = [v for f, v in limpio if f <= objetivo]
        return round((v_ult - prev[-1]) * escala, 4) if prev else None

    return {
        "ultimo": round(v_ult * escala, 4),
        "fecha_ultimo": f_ult.isoformat(),
        "cambio_7d": _hace(7),
        "cambio_30d": _hace(30),
    }


@cached(ttl=300)
def _filas_1816() -> list[dict]:
    """Watch 1816: una fila por bono con el último valor de cada campo + cambios
    de TEA. Una sola query (ventana 45d), pivot en Python. TEA/paridad → %."""
    from core.postgres import get_pool

    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.ticker, s.campo, s.fecha, s.valor,
                       coalesce(i.curva, w.curva, 'otros') AS curva,
                       i.denominacion, i.moneda_pago, i.fecha_vencimiento
                FROM research.mkt_1816_series s
                LEFT JOIN research.mkt_1816_instrumentos i ON i.ticker = s.ticker
                LEFT JOIN research.mkt_1816_watch w ON w.ticker = s.ticker
                WHERE s.campo IN ('tea', 'paridad', 'precioClean', 'duration')
                  AND s.fecha >= current_date - %s
                ORDER BY s.ticker, s.campo, s.fecha
                """,
                (_VENTANA_1816_DIAS,),
            )
            crudo = cur.fetchall()
    except Exception as e:
        logger.warning("copiloto research: series 1816 fallaron (%s)", e)
        return []
    por_bono: dict[str, dict] = {}
    puntos: dict[tuple[str, str], list[tuple]] = {}
    for ticker, campo, fecha, valor, curva, denom, moneda, vto in crudo:
        por_bono.setdefault(ticker, {
            "id": ticker, "grupo": curva, "nombre": denom, "unidad": moneda,
            "vencimiento": vto.isoformat() if vto else None,
        })
        puntos.setdefault((ticker, campo), []).append((fecha, valor))
    filas = []
    for ticker, base in por_bono.items():
        tea = _ultimo_y_cambios(puntos.get((ticker, "tea"), []), escala=100.0)
        par = _ultimo_y_cambios(puntos.get((ticker, "paridad"), []), escala=100.0)
        pc = _ultimo_y_cambios(puntos.get((ticker, "precioClean"), []))
        du = _ultimo_y_cambios(puntos.get((ticker, "duration"), []))
        # columna `dato` COMPACTA (balanza 2026-07-20: la tabla ancha gastaba
        # ~35% en celdas vacías): todo lo del bono en un string denso, sin "-".
        partes = []
        if tea["ultimo"] is not None:
            s = f"TEA {tea['ultimo']:.2f}%"
            if tea["cambio_7d"] is not None:
                s += f" (7d {tea['cambio_7d']:+.2f}pp"
                if tea["cambio_30d"] is not None:
                    s += f" · 30d {tea['cambio_30d']:+.2f}pp"
                s += ")"
            partes.append(s)
        if par["ultimo"] is not None:
            partes.append(f"paridad {par['ultimo']:.2f}%")
        if pc["ultimo"] is not None:
            partes.append(f"precio {pc['ultimo']:.1f}")
        if du["ultimo"] is not None:
            partes.append(f"duration {du['ultimo']:.2f}")
        if base.get("vencimiento"):
            partes.append(f"vence {base['vencimiento']}")
        # el nombre suele repetir el ticker entre paréntesis — se depura
        nombre = (base.get("nombre") or "").replace(f"({ticker})", "").strip()
        filas.append({
            "fuente": "1816", "id": ticker, "nombre": nombre,
            "grupo": base.get("grupo"), "dato": " · ".join(partes) or None,
        })
    filas.sort(key=lambda f: (str(f.get("grupo")), str(f.get("id"))))
    return filas[:_MAX_FILAS]


def _filas_watch_series(sql: str, params: tuple, fuente: str) -> list[dict]:
    """Molde BCRA/FRED: query (id, nombre, grupo, unidad, fecha, valor) ASC →
    una fila por serie con último + cambios."""
    from core.postgres import get_pool

    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            crudo = cur.fetchall()
    except Exception as e:
        logger.warning("copiloto research: watch %s falló (%s)", fuente, e)
        return []
    meta: dict[str, dict] = {}
    puntos: dict[str, list[tuple]] = {}
    for sid, nombre, grupo, unidad, fecha, valor in crudo:
        sid = str(sid)
        meta.setdefault(sid, {"id": sid, "nombre": nombre, "grupo": grupo,
                              "unidad": unidad, "fuente": fuente})
        if fecha is not None:
            puntos.setdefault(sid, []).append((fecha, valor))
    filas = []
    for sid, base in meta.items():
        d = _ultimo_y_cambios(puntos.get(sid, []))
        partes = []
        if d["ultimo"] is not None:
            s = f"último {d['ultimo']:g}"
            if base.get("unidad"):
                s += f" {base['unidad']}"
            partes.append(s)
            if d["cambio_7d"] is not None:
                s2 = f"cambio 7d {d['cambio_7d']:+g}"
                if d["cambio_30d"] is not None:
                    s2 += f" · 30d {d['cambio_30d']:+g}"
                partes.append(s2)
            if d["fecha_ultimo"]:
                partes.append(f"al {d['fecha_ultimo']}")
        filas.append({
            "fuente": fuente, "id": base["id"], "nombre": base.get("nombre"),
            "grupo": base.get("grupo"),
            "dato": " · ".join(partes) or "sin datos cargados",
        })
    filas.sort(key=lambda f: (str(f.get("grupo")), str(f.get("id"))))
    return filas[:_MAX_FILAS]


@cached(ttl=300)
def _filas_bcra() -> list[dict]:
    return _filas_watch_series(
        """
        SELECT w.id_variable, w.etiqueta, w.bloque, w.unidad, s.fecha, s.valor
        FROM research.bcra_watch w
        LEFT JOIN research.bcra_series s
          ON s.id_variable = w.id_variable AND s.fecha >= current_date - %s
        WHERE w.activo ORDER BY w.id_variable, s.fecha
        """,
        (_VENTANA_1816_DIAS,),
        fuente="bcra",
    )


@cached(ttl=300)
def _filas_fred() -> list[dict]:
    return _filas_watch_series(
        """
        SELECT w.series_id, w.etiqueta, w.bloque, w.unidad, s.fecha, s.valor
        FROM research.fred_watch w
        LEFT JOIN research.fred_observations s
          ON s.series_id = w.series_id AND s.fecha >= current_date - %s
        WHERE w.activo ORDER BY w.series_id, s.fecha
        """,
        (_VENTANA_FRED_DIAS,),
        fuente="fred",
    )


def _filas_reportes() -> list[dict]:
    from api.services import research_docs_sql

    docs = (research_docs_sql.listar() or {}).get("documentos") or []
    filas = []
    for d in docs[:_MAX_REPORTES]:
        partes = [p for p in (
            d.get("fecha"), d.get("fuente"),
            f"autor {d.get('autor')}" if d.get("autor") else None,
            (d.get("comentario") or "")[:200] or None,
        ) if p]
        filas.append({"fuente": "reportes", "id": d.get("id"),
                      "nombre": d.get("titulo"), "grupo": d.get("tipo"),
                      "dato": " · ".join(str(p) for p in partes) or None})
    return filas


def _fetch_research(params: dict | None = None) -> list[dict]:
    """TODO el research junto, siempre — el cruce entre fuentes es el valor de
    esta vista. Cada bloque degrada solo ([] si su tabla falla); la tab activa
    viaja en extras como señal de prioridad, no como filtro."""
    return _filas_1816() + _filas_bcra() + _filas_fred() + _filas_reportes()


# ── extras: el research escrito (mails 1816) + CCL ──────────────────────────
def _terminos_busqueda(pregunta: str) -> list[str]:
    """Términos 'buscables' de la pregunta: palabras ≥4 letras fuera del
    stopword-list (+ tickers en mayúscula tal cual). Determinista."""
    vistos, out = set(), []
    for w in re.findall(r"[A-Za-zÁÉÍÓÚáéíóúÑñ0-9]{3,}", pregunta or ""):
        k = w.lower()
        if k in _STOPWORDS or k in vistos or (len(w) < 4 and not w.isupper()):
            continue
        vistos.add(k)
        out.append(w)
    return out[:_FTS_TERMINOS]


def _mail_reciente() -> list[str]:
    from api.services import research_sql

    try:
        items = (research_sql.listar_research(limit=1) or {}).get("items") or []
    except Exception as e:
        logger.warning("copiloto research: mail reciente falló (%s)", e)
        return []
    if not items:
        return ["[research de hoy] todavía no hay mails de 1816 ingestados."]
    m = items[0]
    # el campo es `texto` (el crudo LIMPIO para mostrar) — no `cuerpo`, que no
    # viaja en la salida de listar_research. Bug cazado por la balanza
    # (diag_contexto --pesos): el bloque salía con el mail vacío (19 tokens).
    cuerpo = (m.get("texto") or "").strip()
    if not cuerpo:
        return ["[research de hoy] el mail más reciente vino sin texto legible."]
    if len(cuerpo) > _MAIL_HOY_CHARS:
        cuerpo = cuerpo[:_MAIL_HOY_CHARS] + " (…sigue)"
    return [f"[research más reciente — {m.get('fecha')} · {m.get('asunto')}] {cuerpo}"]


def _fts_mails(pregunta: str) -> list[str]:
    """Fragmentos de mails que MENCIONAN los términos de la pregunta, con fecha.
    El modelo cita SOLO de acá — si no hay resultados, no hay cita posible."""
    from api.services import research_sql

    terminos = _terminos_busqueda(pregunta)
    if not terminos:
        return []
    frags, vistos = [], set()
    for t in terminos:
        try:
            items = (research_sql.buscar_research(t, limit=2) or {}).get("items") or []
        except Exception as e:
            logger.warning("copiloto research: FTS '%s' falló (%s)", t, e)
            continue
        for it in items:
            if it.get("id") in vistos or not it.get("fragmento"):
                continue
            vistos.add(it.get("id"))
            frags.append(f"({it.get('fecha')}) {it.get('fragmento')}")
            if len(frags) >= _FTS_FRAGMENTOS:
                break
        if len(frags) >= _FTS_FRAGMENTOS:
            break
    if not frags:
        return [f"[1816 dijo] sin menciones de {'/'.join(terminos)} en los mails guardados."]
    return ["[1816 dijo — fragmentos de mails que mencionan el tema, con fecha] "
            + " || ".join(frags)]


def _extras_research(
    filas: list[dict], pregunta: str, historial: list[dict], params: dict | None = None
) -> list[str]:
    from api.services import scanner

    tab = _sanear_params_research(params)
    partes = [f"[tab activa] el usuario está parado en '{tab}' "
              f"({datetime.now(UTC).date().isoformat()}) — priorizá esa fuente si la "
              "pregunta es ambigua, pero tenés TODA la tabla para cruzar."]
    partes.extend(_mail_reciente())
    partes.extend(_fts_mails(pregunta))
    try:
        ccl = scanner.get_ccl_live() or {}
        if ccl.get("value") is not None:
            linea = f"[CCL live] {_num(ccl['value'])} ARS/USD"
            if ccl.get("vs_1d_pct") is not None:
                linea += f" · vs cierre anterior {ccl['vs_1d_pct']:+.2f}%"
            partes.append(linea)
    except Exception as e:
        logger.warning("copiloto research: CCL falló (%s)", e)
    return partes


# Compacta a propósito (balanza 2026-07-20: la tabla ancha de 17 columnas
# gastaba ~35% en celdas vacías — cada fuente usa UNA columna `dato` densa).
_COLUMNAS_RESEARCH = [
    ("fuente", "fuente"), ("id", "serie/ticker"), ("nombre", "nombre"),
    ("grupo", "curva/bloque"), ("dato", "datos (ya calculados)"),
]

_REGLAS_RESEARCH = """Sos el copiloto de la vista RESEARCH y sos el ANALISTA INTEGRAL de la \
mesa: sabés de todo a la vez. La tabla trae SIEMPRE las cuatro fuentes juntas (columna \
`fuente`): bonos 1816, variables BCRA, series internacionales FRED y reportes del equipo. \
[tab activa] te dice dónde está parado el usuario — priorizá esa fuente si la pregunta es \
ambigua, pero tu VALOR MÁXIMO es CRUZAR fuentes: la TEA de un bono contra la tasa Fed y el \
riesgo internacional, la brecha contra las reservas del BCRA, lo que dice 1816 contra lo que \
muestran los números. Un analista que mira una sola tabla no es research.

Qué es cada fuente y cómo leer su columna "datos" (todo YA calculado — no recalcules):
- fuente=1816: el watch de bonos de 1816 (curvas soberanas/provinciales/corporativas), \
CIERRE diario. Formato: "TEA 7.31% (7d -0.39pp · 30d +0.59pp) · paridad 97.86% · precio \
154228.5 · duration 1.01 · vence 2027-10-31". La TEA ya está en %; los 7d/30d son el \
CAMBIO de la TEA en puntos porcentuales (negativo = comprimió). Los números son de la API \
de 1816, fuente de verdad.
- fuente=bcra: variables monetarias/cambiarias del BCRA. Formato: "último X <unidad> · \
cambio 7d ±Y · 30d ±Z · al <fecha>" — los cambios son DIFERENCIA ABSOLUTA en la unidad de \
la serie (no %). La fecha "al" manda: si es vieja, decilo.
- fuente=fred: series internacionales (tasas USA, commodities, liquidez), mismo formato \
que bcra. Ojo frecuencia: hay series semanales/mensuales — el cambio 30d puede ser el dato \
anterior.
- fuente=reportes: documentos que cargó el equipo ("fecha · fuente · autor · comentario"). \
NO tenés el contenido de los PDFs — solo la ficha y el comentario; si piden el detalle de \
un PDF, decí que lo abran de la lista.

EL RESEARCH ESCRITO — tu diferencial y tu disciplina más importante:
- [research más reciente] es el último mail de 1816 ("el día en pocas líneas"): tu contexto \
del día. Citalo con su fecha.
- [1816 dijo] son fragmentos REALES de mails guardados que mencionan el tema de la pregunta, \
cada uno con su fecha. Para decir "1816 dijo/viene diciendo X" SOLO podés citar de estos dos \
bloques, SIEMPRE con la fecha ("el 15/07 decían…"). Si el bloque dice "sin menciones", la \
respuesta es que no encontrás menciones en los mails guardados — JAMÁS inventes o \
parafrasees de memoria una opinión de 1816 que no esté en los fragmentos.
- Tu valor máximo es CRUZAR número y narrativa: "la TEA de TX26 comprimió 80pb en el mes y \
1816 venía diciendo (10/07) que el tramo CER estaba barato".

TUS HERRAMIENTAS (usalas, para eso están): si la pregunta necesita la EVOLUCIÓN de un bono \
(un período, "cómo venía en abril", "grafica la TEA del trimestre") usá `serie_de`; si \
pide qué dijo 1816 de un tema que no está en los fragmentos provistos, usá \
`buscar_en_mails`. Preferí pedir el dato exacto antes que responder "no lo tengo" — pero \
si la herramienta tampoco lo trae, ahí sí decilo derecho. Las herramientas son TUYAS y \
son INVISIBLES para el usuario: JAMÁS se las ofrezcas ("si querés uso la herramienta de \
búsqueda"), ni las nombres, ni pidas permiso para usarlas — si hace falta, la usás y \
respondés; el usuario no puede invocar nada. (Caso real 2026-07-20: ofreció "profundizar \
con la herramienta" en vez de buscar él — eso es plomería a la vista, prohibido.)

Reglas de acá:
- Todos los números ya vienen calculados (últimos valores, cambios, %/pp). CERO aritmética \
propia; si un dato no está, no está.
- Método BLUF: conclusión primero, detalle después. Formato análisis (más aire que trading), \
pero sin novela: 6-10 líneas máximo salvo que pidan detalle.
- Pedidos de recomendación: ranking objetivo con criterio explícito (ej. TEA vs duration), \
nunca consejo de inversión personalizado.
- El CCL live es contexto del día, no dato de research."""

# ── TOOLS (piloto function-calling 2026-07-20, canon Anthropic/OpenAI: JIT
# retrieval — el modelo PIDE lo que la pregunta necesita en vez de pre-inyectar
# todo; el código ejecuta y devuelve resultados COMPACTOS) ───────────────────

_TOOLS_RESEARCH = [
    {"type": "function", "function": {
        "name": "buscar_en_mails",
        "description": "Busca un tema en TODOS los mails de research de 1816 guardados y "
                       "devuelve los fragmentos más relevantes CON FECHA. Usala cuando la "
                       "pregunta pida qué dijo/viene diciendo 1816 sobre algo que no está "
                       "en los fragmentos ya provistos.",
        "parameters": {"type": "object", "properties": {
            "tema": {"type": "string", "description": "el tema a buscar (1-3 palabras)"}},
            "required": ["tema"]},
    }},
    {"type": "function", "function": {
        "name": "serie_de",
        "description": "Serie histórica diaria de un bono del watch 1816. Usala cuando "
                       "pidan la evolución/los valores de un período que no está en la "
                       "tabla (la tabla solo trae el último valor y cambios 7/30d).",
        "parameters": {"type": "object", "properties": {
            "ticker": {"type": "string", "description": "ticker del bono (ej. TX26)"},
            "campo": {"type": "string", "enum": ["tea", "paridad", "precioClean", "duration"]},
            "desde": {"type": "string", "description": "YYYY-MM-DD (opcional, default 6 meses)"},
            "hasta": {"type": "string", "description": "YYYY-MM-DD (opcional, default hoy)"}},
            "required": ["ticker", "campo"]},
    }},
]


def _ejecutar_tool_research(nombre: str, args: dict) -> str:
    """Ejecuta una tool del piloto. Resultados COMPACTOS (el gateway capa a 4000
    chars igual). Cualquier error devuelve texto de error — jamás levanta."""
    if nombre == "buscar_en_mails":
        from api.services import research_sql

        items = (research_sql.buscar_research(str(args.get("tema") or ""), limit=4)
                 or {}).get("items") or []
        if not items:
            return "sin menciones de ese tema en los mails guardados."
        return " || ".join(f"({it.get('fecha')}) {it.get('fragmento')}"
                           for it in items if it.get("fragmento"))
    if nombre == "serie_de":
        from api.services import research_1816_sql

        campo = str(args.get("campo") or "tea")
        r = research_1816_sql.series([str(args.get("ticker") or "")], campo,
                                     args.get("desde"), args.get("hasta"))
        series = (r or {}).get("series") or []
        puntos = series[0].get("puntos") if series else None
        if not puntos:
            return "sin datos de esa serie en ese rango."
        escala = 100.0 if campo in ("tea", "paridad") else 1.0
        vals = [(f, v * escala) for f, v in puntos if v is not None]
        if not vals:
            return "sin datos de esa serie en ese rango."
        # resumen + submuestreo a ≤24 puntos (canon: resultados compactos)
        paso = max(1, len(vals) // 24)
        muestra = vals[::paso][-24:]
        mn, mx = min(vals, key=lambda p: p[1]), max(vals, key=lambda p: p[1])
        unidad = "%" if escala == 100.0 else ""
        return (f"{series[0].get('ticker')} {campo}: {len(vals)} ruedas de {vals[0][0]} a "
                f"{vals[-1][0]} · primero {vals[0][1]:.2f}{unidad} · último "
                f"{vals[-1][1]:.2f}{unidad} · mín {mn[1]:.2f}{unidad} ({mn[0]}) · máx "
                f"{mx[1]:.2f}{unidad} ({mx[0]}) · muestra: "
                + ", ".join(f"{f} {v:.2f}" for f, v in muestra))
    return f"herramienta desconocida: {nombre}"


_CHIPS_RESEARCH = [
    {"label": "El día en pocas líneas",
     "pregunta": "Resumime el research más reciente de 1816: los 3-5 puntos que le "
                 "importan a la mesa hoy, con la fecha del mail."},
    {"label": "¿Qué se movió?",
     "pregunta": "¿Qué se movió más en los últimos 7 y 30 días en TODO el research "
                 "(bonos 1816, BCRA e internacional)? Top 5 con números y una "
                 "lectura corta."},
    {"label": "Número + narrativa",
     "pregunta": "Elegí el movimiento más importante de la tabla y cruzalo con lo que "
                 "venían diciendo los mails de 1816 (citá con fecha)."},
    {"label": "Cruce local vs afuera",
     "pregunta": "Cruzá lo local contra lo internacional: ¿el movimiento de las TEAs y "
                 "la brecha va en línea con las tasas USA y el riesgo global, o hay "
                 "divergencia? Números concretos."},
    {"label": "¿Qué estoy viendo?",
     "pregunta": "Explicame qué muestra la pestaña donde estoy parado, qué significa "
                 "cada columna y qué conviene mirar primero hoy."},
]
