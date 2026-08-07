"""Feed Eikon/Workspace — quotes LIVE del subyacente US de cada CEDEAR (PRUEBA).

Mismo patrón que el dólar oficial MAE (`core/dolar_oficial.py`): un script local
en la PC de la oficina (`scripts/eikon_feed_simple.py`, necesita Workspace abierto y
logueado) pollea Eikon y le pega a `POST /api/ingest/eikon/quotes`; la API (este
módulo) persiste en SQL `mercado.eikon_snapshot`. La PC NO toca la base directo.

Es un camino SEPARADO de `mercado.adr_snapshot` (Finnhub cada 15 min): conviven
y de momento nadie lee esta tabla — primero validamos que el dato llegue bien.

El RIC (identidad Refinitiv del subyacente, ej. AAPL.O) vive en
`mercado.cedears.ric` y se carga A MANO desde Manager → TÍTULOS → RENTA VARIABLE
(o `scripts/set_ric.py`). El feed solo LEE los cargados; los sin RIC quedan fuera.
(`set_rics` queda disponible para una futura resolución asistida — solo llena
vacíos, nunca pisa una carga manual. La auto-resolución del feed se quitó
2026-07-16: la symbology devolvía tickers pelados para NYSE y ensució el catálogo.)
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.pg_mirror import write_native
from core.postgres import get_pool

TABLE = "mercado.eikon_snapshot"


def universo_rics() -> list[dict]:
    """Underlyings (US symbol) únicos de los CEDEARs activos + su RIC si lo hay.

    Lo consume `GET /api/ingest/eikon/universo`: es lo que el feed local usa
    como lista de suscripción (los que tienen `ric=None` los resuelve él).
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT upper(COALESCE(underlying, ticker_corto)) AS und, "
            "       max(NULLIF(ric, ''))                      AS ric "
            "FROM mercado.cedears WHERE activo IS TRUE "
            "GROUP BY 1 ORDER BY 1"
        )
        return [{"ticker": t, "ric": r} for t, r in cur.fetchall()]


def set_rics(items: list[dict]) -> int:
    """Persiste RICs resueltos por el feed en `mercado.cedears.ric`.

    Cada item: {ticker: underlying US, ric: 'AAPL.O'}. Solo llena filas SIN ric
    (no pisa cargas manuales). Devuelve cuántos underlyings se actualizaron.
    """
    actualizados = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for item in items:
            ticker = (item.get("ticker") or "").strip().upper()
            ric = (item.get("ric") or "").strip()
            if not ticker or not ric:
                continue
            cur.execute(
                "UPDATE mercado.cedears SET ric = %s "
                "WHERE upper(COALESCE(underlying, ticker_corto)) = %s "
                "  AND (ric IS NULL OR ric = '')",
                (ric, ticker),
            )
            if cur.rowcount:
                actualizados += 1
    return actualizados


def _ccl_implicito(cedear_data, cedear_upd, ratio, adr_last) -> float | None:
    """CCL implícito del papel: (last del CEDEAR en ARS × ratio) ÷ last del ADR
    en USD. REGLA: nunca rompe — si falta CUALQUIER pata (feed Eikon apagado,
    CEDEAR sin operar, sin ratio cargado, dato local que no es de HOY), devuelve
    None y la celda queda vacía."""
    try:
        if not cedear_data or ratio is None or adr_last is None:
            return None
        r = float(ratio)
        adr = float(adr_last)
        cedear = float(cedear_data.get("last") or 0)
        if r <= 0 or adr <= 0 or cedear <= 0:
            return None
        # El last del CEDEAR debe ser de HOY (ART): mezclar un ARS viejo con un
        # USD fresco fabrica un CCL falso — mejor celda vacía.
        if cedear_upd is None:
            return None
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Argentina/Buenos_Aires")
        if cedear_upd.astimezone(tz).date() != datetime.now(tz).date():
            return None
        return cedear * r / adr
    except (TypeError, ValueError, AttributeError):
        return None


def tablero_reuters() -> list[dict]:
    """Filas del tablero TRADING → REUTERS: un activo por fila, SOLO los que el
    feed fue suscribiendo (los que tienen quote en `mercado.eikon_snapshot`),
    con el ratio del CEDEAR del catálogo y el CCL implícito calculado
    SERVER-SIDE (last CEDEAR ARS × ratio ÷ last ADR USD, ver _ccl_implicito)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT e.ticker, e.ric, e.data, e.updated_at, c.ratio, c.rubro, "
            "       cs.data AS cedear_data, cs.updated_at AS cedear_upd "
            "FROM mercado.eikon_snapshot e "
            "LEFT JOIN LATERAL ("
            "  SELECT m.ticker AS cedear_ticker, m.ratio, m.rubro FROM mercado.cedears m "
            "  WHERE upper(COALESCE(m.underlying, m.ticker_corto)) = e.ticker "
            "    AND m.activo IS TRUE "
            "  ORDER BY m.ratio IS NULL, m.ticker LIMIT 1"
            ") c ON TRUE "
            "LEFT JOIN mercado.cedears_snapshot cs ON cs.ticker = c.cedear_ticker "
            "ORDER BY e.ticker")
        # sin "open": CF_OPEN no viene para equities US (verificado 2026-07-16,
        # 27/27 suscriptos sin el dato) — se quitó del feed y de la vista
        campos = [
            "last", "bid", "ask", "high", "low", "prev_close", "volumen",
            "var_pct", "var_neta", "ah_last", "ah_vol", "pre_last",
            "eod_close", "eod_open", "eod_high", "eod_low", "eod_vol",
            "ret_1d", "ret_5d", "ret_wtd", "ret_mtd", "ret_qtd", "ret_ytd",
            "ret_1m", "ret_3m", "ret_1y", "ret_5y",
        ]
        filas = []
        for ticker, ric, data, updated_at, ratio, rubro, cedear_data, cedear_upd in cur.fetchall():
            d = data or {}
            fila = {"ticker": ticker, "ric": ric}
            fila.update({c: d.get(c) for c in campos})
            fila["ah_var_pct"] = _var_pct(d.get("ah_last"), d.get("last"))
            fila["pre_var_pct"] = _var_pct(d.get("pre_last"), d.get("prev_close"))
            fila["ratio"] = float(ratio) if ratio is not None else None
            fila["rubro"] = rubro  # rubro del catálogo de CEDEARs (columna + filtro)
            fila["ccl"] = _ccl_implicito(cedear_data, cedear_upd, ratio, d.get("last"))
            fila["updated_at"] = updated_at
            filas.append(fila)
        return filas


def _var_pct(precio, base) -> float | None:
    """Variación % de `precio` contra `base` (after vs cierre de hoy, pre vs
    cierre anterior). None si falta alguno o la base es 0."""
    try:
        if precio is None or base is None or not float(base):
            return None
        return (float(precio) / float(base) - 1.0) * 100.0
    except (TypeError, ValueError):
        return None


# El rubro NO vive en el feed de Eikon: es la clasificación de negocio propia
# (`mercado.rubros`, editable en Manager → TÍTULOS → RENTA VARIABLE) que ya usa
# el tablero de cotizaciones. Se resuelve del CEDEAR cuyo underlying es el
# ticker del feed — mismo LATERAL que `tablero_reuters`, pero prefiriendo la
# fila que TIENE rubro cargado (en cotizaciones se prefiere la que tiene ratio).
_RUBRO_LATERAL = (
    "LEFT JOIN LATERAL ("
    "  SELECT m.rubro FROM mercado.cedears m "
    "  WHERE upper(COALESCE(m.underlying, m.ticker_corto)) = f.ticker "
    "    AND m.activo IS TRUE "
    "  ORDER BY m.rubro IS NULL, m.ticker LIMIT 1"
    ") c ON TRUE "
)


def tablero_fundamentals() -> list[dict]:
    """Filas del screener FUNDAMENTALS del tab REUTERS: una empresa por fila con
    todas las métricas de la ficha (sin las series históricas, que son pesadas
    y viven en la ficha) + el `rubro` del catálogo propio, para filtrar y
    comparar por sector. Ordenado por ticker."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT f.ticker, f.ric, f.data, f.updated_at, c.rubro "
                    "FROM mercado.eikon_fundamentals f " + _RUBRO_LATERAL +
                    "ORDER BY f.ticker")
        filas = []
        for ticker, ric, data, updated_at, rubro in cur.fetchall():
            d = {k: v for k, v in (data or {}).items()
                 if k not in ("serie_anual", "serie_trimestral")}
            d["ticker"] = ticker
            d["ric"] = ric
            d["rubro"] = rubro
            d["updated_at"] = updated_at
            filas.append(d)
        return filas


# ── AGREGADO del universo (panel superior derecho del screener) ──────────────
# Suma las series históricas de TODAS las empresas suscriptas (o las de un
# rubro) para ver el conjunto en el tiempo: ingresos/EBITDA/resultado/FCF/capex.
# Los MÁRGENES no se suman ni se promedian — se derivan de los montos sumados
# (margen del agregado = Σ utilidad ÷ Σ ingresos), que es el margen real de la
# canasta y no un promedio simple que le da el mismo peso a AAPL que a RKLB.
_AGREGABLES = ("revenue", "gross_profit", "ebitda", "ebit", "net_income",
               "fcf", "capex", "deuda", "caja")
_VENTANA = {"anual": 5, "trimestral": 8}


def _periodo_calendario(fecha: str, modo: str) -> str | None:
    """Fecha de cierre de un período fiscal → etiqueta de calendario.

    Las empresas NO comparten cierre de ejercicio (AAPL cierra en septiembre,
    NVDA en enero), así que sumar "FY2025" mezclaría ventanas distintas. Se
    alinea por el CALENDARIO del cierre: 2025-09-30 → '2025' (anual) o
    '2025Q3' (trimestral). Es la convención estándar para agregados
    cross-company y queda advertida en la vista.
    """
    try:
        anio, mes = int(fecha[:4]), int(fecha[5:7])
    except (TypeError, ValueError, IndexError):
        return None
    if modo == "anual":
        return str(anio)
    return f"{anio}Q{(mes - 1) // 3 + 1}"


def _elegir_ventana(por_ticker: dict[str, dict[str, dict]], largo: int) -> list[str]:
    """Elige QUÉ períodos consecutivos se grafican: los `largo` en los que MÁS
    empresas tienen dato completo.

    Por qué no alcanza con "los últimos N" (bug real 2026-08-07, con el universo
    de 184 empresas): los cierres fiscales están desparramados, así que el
    período más reciente lo tiene solo la minoría que ya reportó. Tomando
    siempre la cola, la canasta constante daba **0 empresas** en trimestral y
    22 de 184 en anual — la vista quedaba vacía. Corriendo la ventana y
    quedándose con la de mayor cobertura, se grafica el tramo donde realmente
    hay datos. Empate → la ventana más reciente.
    """
    todas = sorted({e for filas in por_ticker.values() for e in filas})
    if not todas:
        return []
    if len(todas) <= largo:
        return todas
    mejor: tuple[int, list[str]] | None = None
    for i in range(len(todas) - largo + 1):
        ventana = todas[i:i + largo]
        cobertura = sum(
            1 for filas in por_ticker.values()
            if all(_num(filas.get(e, {}).get("revenue")) is not None for e in ventana))
        # `>=` para que, ante igual cobertura, gane la ventana MÁS RECIENTE
        # (las ventanas se recorren de vieja a nueva).
        if mejor is None or cobertura >= mejor[0]:
            mejor = (cobertura, ventana)
    return mejor[1] if mejor else todas[-largo:]


def agregado_fundamentals(periodo: str = "anual", rubro: str | None = None,
                          canasta: str = "constante") -> dict:
    """Serie AGREGADA del universo del feed: una fila por período de calendario
    con la SUMA de cada métrica sobre las empresas de la canasta.

    Args:
        periodo: 'anual' (últimos 5 años) o 'trimestral' (últimos 8 trimestres).
        rubro: si viene, agrega SOLO las empresas de ese rubro.
        canasta: 'constante' (default) suma únicamente las empresas con
            ingresos en TODOS los períodos de la ventana — así un salto en la
            curva es negocio y no una empresa que entró o salió del feed.
            'todas' suma lo que haya en cada período (más cobertura, menos
            comparable). Las excluidas se devuelven con su motivo.
    """
    modo = "trimestral" if str(periodo).lower().startswith("trim") else "anual"
    sql = ("SELECT f.ticker, f.data "
           "FROM mercado.eikon_fundamentals f " + _RUBRO_LATERAL)
    params: tuple = ()
    if rubro:
        sql += "WHERE c.rubro = %s "
        params = (rubro,)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql + "ORDER BY f.ticker", params)
        series = {ticker: (data or {}) for ticker, data in cur.fetchall()}

    out = _agregar(series, modo=modo, canasta=canasta)
    out["rubro"] = rubro
    return out


def _agregar(series: dict[str, dict], modo: str, canasta: str) -> dict:
    """Núcleo PURO del agregado (sin base): {ticker → doc de fundamentals} →
    serie sumada. Separado para poder testearlo sin Postgres."""
    clave = "serie_trimestral" if modo == "trimestral" else "serie_anual"

    # ticker → {etiqueta de período → fila de métricas}. Si dos cierres fiscales
    # caen en la misma etiqueta (pasa cuando la empresa mueve su ejercicio), se
    # queda el más reciente.
    por_ticker: dict[str, dict[str, dict]] = {}
    ultima_fecha: dict[tuple[str, str], str] = {}
    for ticker, data in series.items():
        for fila in (data or {}).get(clave) or []:
            fecha = str(fila.get("fecha") or "")
            etiqueta = _periodo_calendario(fecha, modo)
            if not etiqueta:
                continue
            previa = ultima_fecha.get((ticker, etiqueta))
            if previa is not None and fecha <= previa:
                continue
            ultima_fecha[(ticker, etiqueta)] = fecha
            por_ticker.setdefault(ticker, {})[etiqueta] = fila

    etiquetas = _elegir_ventana(por_ticker, _VENTANA[modo])

    incluidas, excluidas = [], []
    for ticker in series:
        filas = por_ticker.get(ticker) or {}
        if not filas:
            excluidas.append({"ticker": ticker, "motivo": "sin serie histórica"})
            continue
        completa = all(_num(filas.get(e, {}).get("revenue")) is not None for e in etiquetas)
        if canasta == "constante" and not completa:
            excluidas.append({"ticker": ticker, "motivo": "no cubre todos los períodos"})
            continue
        incluidas.append(ticker)

    puntos = []
    for etiqueta in etiquetas:
        punto: dict = {"periodo": etiqueta, "n": 0}
        for metrica in _AGREGABLES:
            suma, n = 0.0, 0
            for ticker in incluidas:
                v = _num((por_ticker[ticker].get(etiqueta) or {}).get(metrica))
                if v is not None:
                    suma += v
                    n += 1
            punto[metrica] = suma if n else None
            punto[f"{metrica}_n"] = n
        punto["n"] = punto["revenue_n"]
        # Márgenes del AGREGADO: Σ utilidad ÷ Σ ingresos (no promedio de márgenes).
        ingresos = punto.get("revenue")
        for margen, numerador in (("margen_bruto", "gross_profit"),
                                  ("margen_operativo", "ebit"),
                                  ("margen_neto", "net_income")):
            valor = punto.get(numerador)
            punto[margen] = (valor / ingresos * 100.0) if valor is not None and ingresos else None
        puntos.append(punto)

    return {
        "periodo": modo,
        "canasta": canasta,
        "ventana": {"desde": etiquetas[0], "hasta": etiquetas[-1]} if etiquetas else None,
        "universo": len(series),
        "puntos": puntos,
        "empresas": incluidas,
        "excluidas": excluidas,
    }


def _num(v) -> float | None:
    """Valor del jsonb → float, o None si no es un número usable."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def upsert_fundamentals(docs: list[dict]) -> int:
    """Upsertea los fundamentals curados del feed en `mercado.eikon_fundamentals`
    (1 fila por ticker; el feed los manda ~1 vez por día). Mismo contrato que
    upsert_quotes: `updated_at` lo pone el server."""
    if not docs:
        return 0
    now = datetime.now(UTC)
    rows: list[dict] = []
    for data in docs:
        if not isinstance(data, dict):
            continue
        ticker = (data.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        rows.append({
            "ticker":     ticker,
            "ric":        data.get("ric"),
            "data":       data,
            "updated_at": now,
        })
    if not rows:
        return 0
    return write_native("mercado.eikon_fundamentals", ["ticker"], rows)


def ficha(ticker: str) -> dict | None:
    """La FICHA de una empresa del tab REUTERS: quote live (eikon_snapshot) +
    fundamentals curados (eikon_fundamentals) + ratio del CEDEAR + velas
    diarias de 1 año (mercado.precios_acciones, EOD ya en casa) para el chart.
    None si el ticker no existe en ninguna fuente (→ 404)."""
    t = (ticker or "").strip().upper()
    if not t:
        return None
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ric, data, updated_at FROM mercado.eikon_snapshot "
                    "WHERE ticker = %s", (t,))
        q = cur.fetchone()
        cur.execute("SELECT ric, data, updated_at FROM mercado.eikon_fundamentals "
                    "WHERE ticker = %s", (t,))
        f = cur.fetchone()
        cur.execute("SELECT max(ratio) FROM mercado.cedears "
                    "WHERE upper(COALESCE(underlying, ticker_corto)) = %s "
                    "  AND activo IS TRUE", (t,))
        ratio = (cur.fetchone() or [None])[0]
        cur.execute("SELECT fecha, close FROM mercado.precios_acciones "
                    "WHERE ticker = %s AND close IS NOT NULL "
                    "  AND fecha >= CURRENT_DATE - 380 ORDER BY fecha", (t,))
        velas = [{"fecha": fe.isoformat(), "close": float(cl)} for fe, cl in cur.fetchall()]

    if q is None and f is None and not velas:
        return None
    quote = dict(q[1] or {}) if q else {}
    if q:
        quote["updated_at"] = q[2].isoformat() if q[2] else None
        quote["ah_var_pct"] = _var_pct(quote.get("ah_last"), quote.get("last"))
        quote["pre_var_pct"] = _var_pct(quote.get("pre_last"), quote.get("prev_close"))
    fund = dict(f[1] or {}) if f else None
    if fund is not None and f:
        fund["updated_at"] = f[2].isoformat() if f[2] else None
    return {
        "ticker": t,
        "ric": (q and q[0]) or (f and f[0]),
        "ratio": float(ratio) if ratio is not None else None,
        "quote": quote or None,
        "fundamentals": fund,
        "velas": velas,
    }


def upsert_quotes(docs: list[dict]) -> int:
    """Upsertea quotes del feed en `mercado.eikon_snapshot` (1 fila por ticker,
    no acumula histórico). `updated_at` lo pone el server (no se confía en el
    reloj de la PC de oficina). Devuelve cuántos se escribieron."""
    if not docs:
        return 0
    now = datetime.now(UTC)
    rows: list[dict] = []
    for data in docs:
        if not isinstance(data, dict):
            continue
        ticker = (data.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        rows.append({
            "ticker":     ticker,
            "ric":        data.get("ric"),
            "data":       data,          # dict → jsonb passthrough
            "updated_at": now,
        })
    if not rows:
        return 0
    return write_native(TABLE, ["ticker"], rows)
