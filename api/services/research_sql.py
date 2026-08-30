"""api/services/research_sql.py — lectura del research diario (mails de 1816) para
la vista RESEARCH (pilar B, Nivel 1). Doc madre: docs/VISTA_RESEARCH.md.

Lee `ia.research` (lo escribe jobs/research_mail.py): el mail CRUDO, que es lo
único que hay — el DESTILADO del LLM se borró el 2026-08-28 (nunca corrió y
ninguna pantalla lo dibujaba). Puro (sin FastAPI). El `tipo` (diario/mensual) se
deriva del asunto al vuelo — todavía no es columna (ver docs/VISTA_RESEARCH.md §5.2).
"""
from __future__ import annotations

import logging
import re

from api.cache import cached
from core.postgres import get_pool

logger = logging.getLogger(__name__)

_MAX_LIMIT = 100

# Limpieza para MOSTRAR el research lindo: el crudo (fuente de verdad, intacto en
# la DB) trae basura del reenvío/HTML (headers, imágenes [url], masthead repetido,
# separadores, pie). Se saca al servir — no se toca el `cuerpo` guardado (FTS/citas).
_HEADER_LINE = re.compile(
    r"^\s*(de|para|asunto|enviados?|sent|from|to|cc|subject|date|fecha)\s*:", re.IGNORECASE
)
_FOOTER_MARKERS = (
    "cualquier duda estamos a disposici",   # "...disposición/disposicion"
    "1816 | econom",
    "www.1816.com.ar",
    "copyright ©",
    "copyright (c)",
    "you can unsubscribe",
    "unsubscribe from this list",
)
# Links: primero los envueltos ([url] de imagen, (url), <url>), después los sueltos.
# El suelto NO se come la puntuación final de la frase (deja el "." del "leerse acá.")
# para no fundir dos items en un párrafo.
_URL_WRAPPED = re.compile(r"[\[(<]\s*(?:https?://|www\.)[^\])>]*[\])>]", re.IGNORECASE)
_URL_BARE = re.compile(r"(?:https?://|www\.)\S*[^\s.,;:!?]", re.IGNORECASE)
_SEP_LINE = re.compile(r"^[\s_\-=–—─.·*]{5,}$")             # línea de separación
_TITULO = re.compile(r"EL D[IÍ]A EN POCAS L[IÍ]NEAS", re.IGNORECASE)
_MASTHEAD_INLINE = re.compile(                              # "16 de julio de 2026 EL DÍA…"
    r"\d{1,2}\s+de\s+[a-záéíóúñ]+\s+de\s+\d{4}\s+EL D[IÍ]A EN POCAS L[IÍ]NEAS", re.IGNORECASE
)
_SOLO_FECHA = re.compile(r"^\d{1,2}\s+de\s+[a-záéíóúñ]+\s+de\s+\d{4}$", re.IGNORECASE)
# TITULAR de 1816 = 4+ palabras seguidas SIN minúsculas → separa items en párrafos
# (el mail viene como un bloque) sin partir acrónimos sueltos (USD/BCRA/MLC).
_ANTES_TITULAR = re.compile(
    r"(?<=[.;:])\s+(?=(?:[A-ZÁÉÍÓÚÑ0-9$«][^\sa-záéíóúñ]*\s+){3,}[A-ZÁÉÍÓÚÑ])"
)


def _limpiar_para_mostrar(cuerpo: str | None) -> str:
    """Crudo del mail → research legible y en párrafos. Saca: pie (Copyright/
    unsubscribe), headers del reenvío (De:/Para:/…), imágenes [url], separadores,
    el masthead repetido (fecha + 'EL DÍA EN POCAS LÍNEAS') y líneas duplicadas.
    Después separa los items en párrafos por su titular en mayúscula. Conservador:
    ante la duda deja el texto. El `cuerpo` de la DB no se toca."""
    if not cuerpo:
        return ""
    lineas = cuerpo.splitlines()
    corte = len(lineas)
    for i, ln in enumerate(lineas):
        if any(mk in ln.lower() for mk in _FOOTER_MARKERS):
            corte = i
            break
    out: list[str] = []
    prev = ""
    for ln in lineas[:corte]:
        ln = _URL_WRAPPED.sub("", ln)       # [url]/(url)/<url> (imágenes, etc.)
        ln = _URL_BARE.sub("", ln)          # links sueltos (deja el punto de la frase)
        ln = _MASTHEAD_INLINE.sub("", ln)   # masthead embebido (fecha+título)
        ln = _TITULO.sub("", ln)            # el título suelto (redundante con la card)
        if _HEADER_LINE.match(ln):
            continue
        s = " ".join(ln.split())            # colapsa espacios/tabs internos
        if not s:
            if prev:                        # UNA línea en blanco entre bloques
                out.append("")
                prev = ""
            continue
        if _SEP_LINE.match(s) or _SOLO_FECHA.match(s) or s.lower().startswith(("http://", "https://")):
            continue
        if s == prev:                       # dedupe de líneas repetidas
            continue
        out.append(s)
        prev = s
    texto = "\n".join(out)
    texto = re.sub(r"\s+([.,;:])", r"\1", texto)   # espacio huérfano antes de puntuación
    texto = _ANTES_TITULAR.sub("\n\n", texto)      # separar items en párrafos
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def _fuente_label(asunto: str | None, cuerpo: str | None) -> str:
    """La FUENTE del reporte para el selector de la vista (1816 / ACA VALORES / …).
    Se detecta por contenido, no por el From (que es el que reenvía). Hoy todo es
    1816; se amplía cuando entren otras fuentes."""
    hay = f"{asunto or ''} {(cuerpo or '')[:600]}".lower()
    if "1816" in hay:
        return "1816"
    return "Otra"


def _tipo_de_asunto(asunto: str | None) -> str:
    """diario / mensual / otro — heurística sobre el asunto (sin columna todavía).
    'El día en pocas líneas' (aunque venga reenviado con 'RV:'/'Fwd:') = diario."""
    a = (asunto or "").lower()
    if "pocas l" in a:              # "pocas líneas" / "pocas lineas"
        return "diario"
    if "mensual" in a or "informe" in a:
        return "mensual"
    return "otro"


def _fila(r: dict) -> dict:
    return {
        "id": r["id"],
        "fecha": r["fecha"].isoformat() if r.get("fecha") else None,
        "fuente": r.get("fuente"),
        "asunto": r.get("asunto"),
        "tipo": _tipo_de_asunto(r.get("asunto")),
        "fuente_label": _fuente_label(r.get("asunto"), r.get("cuerpo")),
        # `texto` = crudo limpio para MOSTRAR (sin headers/pie del reenvío). El
        # crudo original queda en la DB (fuente de verdad, FTS, citas).
        "texto": _limpiar_para_mostrar(r.get("cuerpo")),
    }


@cached(ttl=60)
def listar_research(limit: int = 30, offset: int = 0) -> dict:
    """Timeline del research por fecha (más nuevo primero), con el texto limpio.
    Cache 60s (perf 2026-07-18): cada visita a /research lo pedía por SSR y
    re-leía + re-limpiaba 30 cuerpos; los mails cambian ~1 vez por día.
    OJO @cached → SIEMPRE invocar con kwargs (regla del repo)."""
    limit = max(1, min(int(limit), _MAX_LIMIT))
    offset = max(0, int(offset))
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, fecha, fuente, asunto, cuerpo "
                "FROM ia.research ORDER BY fecha DESC, id DESC "
                "LIMIT %s OFFSET %s",
                (limit, offset),
            )
            cols = [c.name for c in cur.description]
            items = [_fila(dict(zip(cols, row, strict=True))) for row in cur.fetchall()]
            cur.execute("SELECT count(*) FROM ia.research")
            total = cur.fetchone()[0]
    except Exception as e:
        logger.warning("research_sql.listar_research falló (%s)", e)
        return {"items": [], "total": 0}
    return {"items": items, "total": total}
