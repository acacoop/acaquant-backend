"""copiloto/research.py — vista RESEARCH unificada (decisión user 2026-07-20):
UN copiloto para toda /research; el contexto cambia según la tab activa
(params.tab). Doc madre: docs/VISTA_RESEARCH.md (§ IA Nivel 2) + docs/COPILOTO.md.

Tabs → tabla TSV:
  - argentina     → watch 1816: último TEA/paridad/precio/duration por bono +
                    cambios 7d/30d (diferencia absoluta, precalculada).
  - bcra          → variables BCRA del watch: último valor + cambios 7d/30d.
  - internacional → series FRED del watch: ídem.
  - reportes      → documentos cargados (título, fecha, tipo, comentario).
(rv-int tiene su propio copiloto, vista `reuters` — no pasa por acá.)

Extras SIEMPRE: el mail de research más reciente ("el día en pocas líneas") +
búsqueda full-text determinista sobre los mails con los términos de la PREGUNTA
(el modelo solo puede citar fragmentos que el código trajo, cada uno con fecha)
+ CCL live. TEA/paridad de 1816 vienen en FRACCIÓN → acá se pasan a % (cero
aritmética del modelo).
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

from .base import _num

logger = logging.getLogger(__name__)

_TABS = ("argentina", "bcra", "internacional", "reportes")
_MAX_FILAS = 120          # techo defensivo de filas de la tabla por tab
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
        filas.append({
            **base,
            "fecha_ultimo": tea["fecha_ultimo"] or pc["fecha_ultimo"],
            "tea": tea["ultimo"], "tea_cambio_7d": tea["cambio_7d"],
            "tea_cambio_30d": tea["cambio_30d"],
            "paridad": par["ultimo"], "precio_clean": pc["ultimo"],
            "duration": du["ultimo"],
        })
    filas.sort(key=lambda f: (str(f.get("grupo")), str(f.get("id"))))
    return filas[:_MAX_FILAS]


def _filas_watch_series(sql: str, params: tuple, escala_pct: bool = False) -> list[dict]:
    """Molde BCRA/FRED: query (id, nombre, grupo, unidad, fecha, valor) ASC →
    una fila por serie con último + cambios."""
    from core.postgres import get_pool

    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            crudo = cur.fetchall()
    except Exception as e:
        logger.warning("copiloto research: watch falló (%s)", e)
        return []
    meta: dict[str, dict] = {}
    puntos: dict[str, list[tuple]] = {}
    for sid, nombre, grupo, unidad, fecha, valor in crudo:
        sid = str(sid)
        meta.setdefault(sid, {"id": sid, "nombre": nombre, "grupo": grupo, "unidad": unidad})
        if fecha is not None:
            puntos.setdefault(sid, []).append((fecha, valor))
    filas = []
    for sid, base in meta.items():
        d = _ultimo_y_cambios(puntos.get(sid, []))
        filas.append({**base, "ultimo": d["ultimo"], "fecha_ultimo": d["fecha_ultimo"],
                      "cambio_7d": d["cambio_7d"], "cambio_30d": d["cambio_30d"]})
    filas.sort(key=lambda f: (str(f.get("grupo")), str(f.get("id"))))
    return filas[:_MAX_FILAS]


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
    )


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
    )


def _filas_reportes() -> list[dict]:
    from api.services import research_docs_sql

    docs = (research_docs_sql.listar() or {}).get("documentos") or []
    return [{
        "id": d.get("id"), "nombre": d.get("titulo"), "grupo": d.get("tipo"),
        "unidad": d.get("fuente"), "fecha_ultimo": d.get("fecha"),
        "autor": d.get("autor"),
        "comentario": (d.get("comentario") or "")[:300] or None,
    } for d in docs[:_MAX_FILAS]]


def _fetch_research(params: dict | None = None) -> list[dict]:
    tab = _sanear_params_research(params)
    if tab == "bcra":
        return _filas_bcra()
    if tab == "internacional":
        return _filas_fred()
    if tab == "reportes":
        return _filas_reportes()
    return _filas_1816()


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
    cuerpo = (m.get("cuerpo") or "").strip()
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
    partes = [f"[tab activa] {tab} — la tabla de arriba es lo que el usuario ve en esa tab "
              f"({datetime.now(UTC).date().isoformat()})."]
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


_COLUMNAS_RESEARCH = [
    ("id", "serie/ticker"), ("nombre", "nombre"), ("grupo", "curva/bloque"),
    ("unidad", "unidad/moneda"), ("fecha_ultimo", "fecha_último_dato"),
    ("ultimo", "último_valor"), ("cambio_7d", "cambio_7d"), ("cambio_30d", "cambio_30d"),
    ("tea", "TEA_%"), ("tea_cambio_7d", "TEA_cambio_7d_pp"),
    ("tea_cambio_30d", "TEA_cambio_30d_pp"), ("paridad", "paridad_%"),
    ("precio_clean", "precio_clean"), ("duration", "duration"),
    ("vencimiento", "vencimiento"), ("autor", "autor"),
    ("comentario", "comentario_del_equipo"),
]

_REGLAS_RESEARCH = """Sos el copiloto de la vista RESEARCH: acá el usuario ANALIZA (no opera \
intradía) — series de renta fija argentina de 1816, variables del BCRA, datos internacionales \
(FRED), reportes cargados por el equipo y los mails diarios de research de 1816. UN solo \
copiloto para toda la vista: [tab activa] te dice qué está mirando y la tabla es ESA tab.

Qué es cada tab y qué columnas aplican:
- argentina: el watch de bonos de 1816 (curvas soberanas/provinciales/corporativas). Columnas: \
TEA_% (tasa efectiva anual, YA en %), sus cambios en PUNTOS PORCENTUALES a 7/30 días, \
paridad_%, precio_clean, duration. Datos de CIERRE diario (fecha_último_dato manda — si es \
vieja, decilo). Los números son de la API de 1816, fuente de verdad; no los recalcules.
- bcra: variables monetarias/cambiarias del BCRA. último_valor en la unidad de la serie; \
cambio_7d/30d son DIFERENCIA ABSOLUTA en esa unidad (no %).
- internacional: series FRED (tasas USA, commodities, liquidez). Misma semántica que bcra. \
Ojo frecuencia: hay series semanales/mensuales — el cambio_30d puede ser el dato anterior.
- reportes: documentos que cargó el equipo (título, tipo, fecha, autor y su comentario). NO \
tenés el contenido de los PDFs — solo la ficha y el comentario; si piden el detalle de un \
PDF, decí que lo abran de la lista.

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

Reglas de acá:
- Todos los números ya vienen calculados (últimos valores, cambios, %/pp). CERO aritmética \
propia; si un dato no está, no está.
- Método BLUF: conclusión primero, detalle después. Formato análisis (más aire que trading), \
pero sin novela: 6-10 líneas máximo salvo que pidan detalle.
- Pedidos de recomendación: ranking objetivo con criterio explícito (ej. TEA vs duration), \
nunca consejo de inversión personalizado.
- El CCL live es contexto del día, no dato de research."""

_CHIPS_RESEARCH = [
    {"label": "El día en pocas líneas",
     "pregunta": "Resumime el research más reciente de 1816: los 3-5 puntos que le "
                 "importan a la mesa hoy, con la fecha del mail."},
    {"label": "¿Qué se movió?",
     "pregunta": "Con la tabla de esta tab: ¿qué series/bonos se movieron más en los "
                 "últimos 7 y 30 días? Top 5 con números y una lectura corta."},
    {"label": "Número + narrativa",
     "pregunta": "Elegí el movimiento más importante de la tabla y cruzalo con lo que "
                 "venían diciendo los mails de 1816 (citá con fecha)."},
    {"label": "¿Qué estoy viendo?",
     "pregunta": "Explicame qué muestra esta pestaña, qué significa cada columna y qué "
                 "conviene mirar primero hoy."},
]
