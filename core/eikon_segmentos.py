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


# Códigos de las filas de AJUSTE (eliminaciones / corporate). En la ficha de UNA
# empresa se muestran (hacen cerrar la cuenta contra sus ingresos totales), pero
# en el ranking AGREGADO del universo son ruido: "Eliminations" no es un negocio
# ni un país. Se excluyen del ranking y se informan aparte.
CODIGOS_AJUSTE = {"ICELIM", "EXPOTH"}


def agregado_segmentos(tipo: str = "negocio", periodo: str = "anual",
                       rubro: str | None = None, tickers: list[str] | None = None,
                       limite: int = 30) -> dict:
    """De dónde sale la plata en TODO el universo (o en el rubro / las empresas
    filtradas): suma el ÚLTIMO período disponible de cada empresa por segmento.

    Se usa el último período DE CADA EMPRESA (no una fecha común): los cierres
    fiscales no coinciden y esperar a que todas tengan la misma fecha dejaría el
    panel vacío — el mismo problema que tuvo el agregado de resultados. Es la
    foto "lo más fresco de cada una", y por eso viaja `fechas` con el rango real
    que se está sumando.

    Para `tipo='geografico'` los nombres se repiten entre empresas (United
    States, China…) y la suma es directamente interpretable. Para
    `tipo='negocio'` cada empresa nombra sus segmentos a su manera, así que el
    ranking es por (segmento) pero cada fila dice de qué empresas viene.
    """
    tp = tipo if tipo in TIPOS else "negocio"
    pe = periodo if periodo in PERIODOS else "anual"
    limpios = [t.strip().upper() for t in (tickers or []) if t and t.strip()]

    def where_de(alias: str) -> tuple[str, list]:
        """Mismos filtros para la subconsulta y para la query externa. Se arma
        con el alias como parámetro (nada de `replace` sobre el SQL: cambiar un
        filtro después rompería la sustitución sin que nadie se entere)."""
        cond, par = [f"{alias}.tipo = %s", f"{alias}.periodo = %s"], [tp, pe]
        if rubro:
            cond.append(
                "EXISTS (SELECT 1 FROM mercado.cedears m "
                "        WHERE upper(COALESCE(m.underlying, m.ticker_corto)) = "
                f"              {alias}.ticker "
                "          AND m.activo IS TRUE AND m.rubro = %s)")
            par.append(rubro)
        if limpios:
            cond.append(f"{alias}.ticker = ANY(%s)")
            par.append(limpios)
        return " AND ".join(cond), par

    w_int, p_int = where_de("s2")
    w_ext, p_ext = where_de("s")
    # La subconsulta marca, por empresa, cuál es su último período; el WHERE
    # exterior se queda solo con las filas de ese período.
    sql = (
        "SELECT s.ticker, s.segmento, s.codigo, s.ingresos, s.fecha "
        "FROM mercado.eikon_segmentos s "
        "JOIN (SELECT ticker, max(fecha) AS fecha FROM mercado.eikon_segmentos s2 "
        f"      WHERE {w_int} GROUP BY ticker) u "
        "  ON u.ticker = s.ticker AND u.fecha = s.fecha "
        f"WHERE {w_ext}")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, [*p_int, *p_ext])
        filas = cur.fetchall()

    acum: dict[str, dict] = {}
    ajustes = 0.0
    fechas: list[str] = []
    empresas: set[str] = set()
    for ticker, segmento, codigo, ingresos, fecha in filas:
        valor = float(ingresos) if ingresos is not None else 0.0
        empresas.add(ticker)
        fechas.append(fecha.isoformat())
        if (codigo or "").strip().upper() in CODIGOS_AJUSTE:
            ajustes += valor
            continue
        fila = acum.setdefault(segmento, {"segmento": segmento, "ingresos": 0.0,
                                          "tickers": []})
        fila["ingresos"] += valor
        fila["tickers"].append((valor, ticker))

    total = sum(f["ingresos"] for f in acum.values())
    ordenadas = sorted(acum.values(), key=lambda f: f["ingresos"], reverse=True)
    out = []
    for f in ordenadas[:limite]:
        contribuyentes = [t for _v, t in sorted(f["tickers"], reverse=True)]
        out.append({
            "segmento": f["segmento"],
            "ingresos": f["ingresos"],
            "share":    (f["ingresos"] / total * 100.0) if total else None,
            "empresas": len(contribuyentes),
            "tickers":  contribuyentes[:4],
        })
    resto = ordenadas[limite:]
    return {
        "tipo": tp,
        "periodo": pe,
        "rubro": rubro,
        "total": total,
        "filas": out,
        "otros": {"ingresos": sum(f["ingresos"] for f in resto), "segmentos": len(resto)}
                 if resto else None,
        "ajustes": ajustes,
        "empresas": len(empresas),
        "fechas": {"desde": min(fechas), "hasta": max(fechas)} if fechas else None,
    }


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
