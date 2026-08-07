"""Ingresos POR SEGMENTO del feed Eikon (familia TR.BGS.*).

Desglose de las ventas de cada empresa: por SEGMENTO DE NEGOCIO (`tipo='negocio'`,
`TR.BGS.BusTotalRevenue`) y por REGIÓN (`tipo='geografico'`,
`TR.BGS.GeoTotalRevenue`). Lo baja el feed de la oficina 1 vez por día
(`scripts/eikon_feed_simple.py`) → `POST /api/ingest/eikon/segmentos` → tabla
`mercado.eikon_segmentos`. La PC no toca la base (mismo patrón que el resto del
feed).

Todo lo de acá está VALIDADO EN VIVO (2026-08-07, discovery sobre
AAPL.O/NVDA.O/KO.N/RKLB.O — ver docs/INTEGRACION_REUTERS.md §8). Tres cosas que
NO son inferibles y que este módulo encapsula:

1. **Las filas de TOTAL no se guardan.** Reuters devuelve, además de los
   segmentos, `Segment Total` (código SEGMTL) y `Consolidated Total` (CONSTL).
   Sumarlas duplicaría todo. Se descartan en la ingesta, no en la vista, para
   que nadie pueda leer la tabla mal.
2. **Las filas de AJUSTE sí se guardan**: `Eliminations` (ICELIM, ventas entre
   segmentos — puede ser NEGATIVA) y `Corporate` (EXPOTH). Sin ellas la suma no
   cierra: KO tiene Σ segmentos 48.806 pero consolidado 47.941 (−1.009 de
   eliminaciones +144 de corporate). Con ellas, Σ(lo guardado) = consolidado
   EXACTO en los 4 casos verificados.
3. **"Segmento de negocio" no siempre es producto.** Apple y Coca-Cola reportan
   sus segmentos por REGIÓN (Americas / Europe / Greater China…), NVDA y Rocket
   Lab por producto. Se guarda lo que la empresa publica; la vista no promete
   "por producto".
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.pg_mirror import write_native
from core.postgres import get_pool

TABLE = "mercado.eikon_segmentos"

# Códigos de las filas de TOTAL — se descartan (ver docstring, punto 1).
CODIGOS_TOTAL = {"SEGMTL", "CONSTL"}
# Nombres de esas mismas filas, por si el segmentCode viene vacío en algún
# instrumento (el código es el criterio primario; esto es la red).
NOMBRES_TOTAL = {"segment total", "consolidated total"}

TIPOS = ("negocio", "geografico")
PERIODOS = ("anual", "trimestral")


def es_total(segmento: str, codigo: str | None) -> bool:
    """¿Es una fila de TOTAL (a descartar) y no un segmento real?"""
    if (codigo or "").strip().upper() in CODIGOS_TOTAL:
        return True
    return (segmento or "").strip().lower() in NOMBRES_TOTAL


def upsert_segmentos(docs: list[dict]) -> int:
    """Upsertea el desglose que manda el feed en `mercado.eikon_segmentos`.

    Cada doc: {ticker, tipo, periodo, fecha, segmento, codigo, orden, ingresos}.
    Las filas de total se DESCARTAN acá (no llegan a la base). `updated_at` lo
    pone el server — no se confía en el reloj de la PC de oficina.
    """
    rows = preparar_filas(docs)
    if not rows:
        return 0
    now = datetime.now(UTC)
    for r in rows:
        r["updated_at"] = now
    return write_native(TABLE, ["ticker", "tipo", "periodo", "fecha", "segmento"], rows)


def preparar_filas(docs: list[dict]) -> list[dict]:
    """Payload crudo del feed → filas listas para la tabla (parte PURA, sin
    base: valida, descarta totales y deduplica). Separada para poder testear
    el criterio sin Postgres."""
    if not docs:
        return []
    rows: list[dict] = []
    vistos: set[tuple] = set()
    for d in docs:
        if not isinstance(d, dict):
            continue
        ticker = (d.get("ticker") or "").strip().upper()
        tipo = (d.get("tipo") or "").strip().lower()
        periodo = (d.get("periodo") or "").strip().lower()
        fecha = (d.get("fecha") or "").strip() if isinstance(d.get("fecha"), str) else None
        segmento = (d.get("segmento") or "").strip()
        codigo = (d.get("codigo") or "").strip() or None
        if not (ticker and segmento and fecha) or tipo not in TIPOS or periodo not in PERIODOS:
            continue
        if es_total(segmento, codigo):
            continue
        ingresos = _num(d.get("ingresos"))
        if ingresos is None:
            continue
        # La PK es (ticker, tipo, periodo, fecha, segmento): si el mismo payload
        # repite una clave, el INSERT ... ON CONFLICT falla ("cannot affect row
        # a second time"). Gana la última.
        clave = (ticker, tipo, periodo, fecha, segmento)
        if clave in vistos:
            rows = [r for r in rows if (r["ticker"], r["tipo"], r["periodo"],
                                        r["fecha"], r["segmento"]) != clave]
        vistos.add(clave)
        rows.append({
            "ticker":     ticker,
            "tipo":       tipo,
            "periodo":    periodo,
            "fecha":      fecha,
            "segmento":   segmento,
            "codigo":     codigo,
            "orden":      _int(d.get("orden")),
            "ingresos":   ingresos,
        })
    return rows


def segmentos(ticker: str, tipo: str = "negocio", periodo: str = "anual") -> dict:
    """Desglose de ingresos de UNA empresa, listo para graficar apilado.

    Returns:
        {ticker, tipo, periodo, segmentos: [nombres...], puntos: [{fecha,
        total, valores: {segmento: ingresos}}], ajustes: [segmentos que son
        eliminaciones/corporate y no negocio]}

    `segmentos` es la UNIÓN ordenada de todos los que aparecen en la ventana:
    Reuters re-expresa los nombres, así que un segmento puede existir en unos
    períodos y en otros no — la vista tiene que bancar los huecos.
    """
    t = (ticker or "").strip().upper()
    tp = tipo if tipo in TIPOS else "negocio"
    pe = periodo if periodo in PERIODOS else "anual"
    if not t:
        return {"ticker": t, "tipo": tp, "periodo": pe,
                "segmentos": [], "puntos": [], "ajustes": []}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT fecha, segmento, codigo, orden, ingresos FROM mercado.eikon_segmentos "
            "WHERE ticker = %s AND tipo = %s AND periodo = %s "
            "ORDER BY fecha, orden NULLS LAST, segmento",
            (t, tp, pe))
        filas = cur.fetchall()

    puntos: list[dict] = []
    por_fecha: dict[str, dict] = {}
    orden_seg: dict[str, tuple] = {}
    ajustes: set[str] = set()
    for fecha, segmento, codigo, orden, ingresos in filas:
        clave = fecha.isoformat()
        punto = por_fecha.get(clave)
        if punto is None:
            punto = {"fecha": clave, "total": 0.0, "valores": {}}
            por_fecha[clave] = punto
            puntos.append(punto)
        valor = float(ingresos) if ingresos is not None else 0.0
        punto["valores"][segmento] = valor
        punto["total"] += valor
        # Orden estable: el de la memoria de la empresa (orden), con el nombre
        # como desempate. Se queda el MENOR visto — así el segmento no salta de
        # lugar entre períodos.
        cand = (orden if orden is not None else 9999, segmento)
        if segmento not in orden_seg or cand < orden_seg[segmento]:
            orden_seg[segmento] = cand
        if (codigo or "").strip().upper() in ("ICELIM", "EXPOTH"):
            ajustes.add(segmento)

    nombres = sorted(orden_seg, key=lambda s: orden_seg[s])
    return {
        "ticker": t,
        "tipo": tp,
        "periodo": pe,
        "segmentos": nombres,
        "puntos": puntos,
        "ajustes": sorted(ajustes),
    }


def tickers_con_segmentos() -> list[str]:
    """Underlyings que ya tienen desglose bajado (para que la vista sepa si el
    panel tiene sentido antes de pedirlo)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ticker FROM mercado.eikon_segmentos ORDER BY ticker")
        return [t for (t,) in cur.fetchall()]


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _int(v) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None
