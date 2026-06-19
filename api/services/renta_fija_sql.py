"""api/services/renta_fija_sql.py — renta fija LIVE leyendo Postgres (mercado.*).

4° corte read-side de MERCADO. Espejo SQL-native de las funciones de
`api/services/renta_fija.py` que leen estado de mercado:

  - get_renta_fija     ← mercado.market_snapshot          (snapshot live + TC BE)
  - listar_curva       ← mercado.curvas + market_snapshot  (curva enriquecida)
  - get_historico_curva← mercado.snapshots_cierre_hist (+ fallback live de hoy)

Todo lo NO migrado se reexporta del módulo Mongo → drop-in del selector:
  - get_historico_trades   → Trading.TimeSales (stream, no migrado, se queda Mongo)
  - calendario_ons / get_retorno_total_data → derivados (Mongo por ahora)
  - resolver_ticker_exacto / _CURVAS_VALIDAS → helpers compartidos

Híbridos a propósito (mismo criterio que macro_sql delega mep/ccl a Mongo):
  - `_bonos_cer_fijados` se REUSA del path Mongo: necesita Trading.DiasHabiles, que
    NO está espejado en SQL. Es barato (cacheado 30s) → no justifica migrar DiasHabiles.
  - MEP live (`macro.get_ultimo_mep`) → Valuaciones.Dolar live, no es series_macro.

REGLA #1 del 19/6 (Mongo se apaga; SQL nativo; lo muerto se borra, no se migra):
  - `total_money` (volumen $ del día) está MUERTO — el motor lo dejó de escribir
    (engines/valores.py::_calcular_metricas: "removido: total_money"). NO se arrastra:
    se quita `total_money_dia` del output y el orden 'volumen_dia' pasa a ordenar por
    el volumen VIVO (`total_nominals_dia`), no por un 0 fantasma como hacía Mongo.

Dependencias Mongo que faltan migrar para que esto sea 100% SQL-native (ver bitácora
docs/2026-06-19.md): `DiasHabiles` (CER fijado) y el MEP live (DolarSnapshot). Mientras
tanto se reusan del path Mongo, marcado abajo con `# TODO SQL-native`.

Dual-run flag `RENTA_FIJA_SQL` (+ `?_engine` override). Validación: spot-check funcional
SQL (no byte-parity contra Mongo — Mongo se va). Se apoya en el dual-write de
market_snapshot (SNAPSHOT_SQL) en paridad (recon 2026-06-19, fresco a ~1s).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from psycopg.rows import dict_row

from api.cache import cached
from core.postgres import get_pool

# Helpers PUROS + passthroughs del path Mongo (drop-in del selector).
from api.services.renta_fija import (  # noqa: F401  (reexport intencional)
    _CURVAS_VALIDAS,
    _ORDENES_VALIDOS,
    _bonos_cer_fijados,
    _es_curva_on,
    _tc_breakeven,
    calendario_ons,
    get_historico_trades,
    get_retorno_total_data,
    resolver_ticker_exacto,
)


def _f(v) -> float | None:
    """numeric (Decimal) → float; None pasa. Mongo devuelve float → paridad."""
    return float(v) if v is not None else None


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _ilike_param(instrumento: str) -> str:
    """Substring case-insensitive (== regex re.escape de Mongo): escapa los
    comodines LIKO (% _ \\) para que el input se trate literal."""
    esc = instrumento.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


# ─────────────────────────────────────────────────────────────────────────────
# get_renta_fija — snapshot live (mercado.market_snapshot)
# ─────────────────────────────────────────────────────────────────────────────

# SQL col → key de salida (Mongo usa metrics.TEA/TEM en mayúscula).
_METRIC_COLS = [
    ("total_nominals", "total_nominals"), ("vwap", "vwap"),
    ("last_price", "last_price"), ("open_price", "open_price"),
    ("high_price", "high_price"), ("low_price", "low_price"),
    ("closing_price", "closing_price"),
    ("tea", "TEA"), ("tem", "TEM"), ("duration", "duration"),
    ("mod_duration", "mod_duration"), ("convexity", "convexity"),
    ("paridad", "paridad"),
]


@cached(ttl=10)
def get_renta_fija(instrumento: str | None = None) -> list:
    """Espejo SQL de renta_fija.get_renta_fija. Lee mercado.market_snapshot;
    enriquece tasa_fija (nativa + CER fijado) con flujo_vencimiento + TC breakeven.
    `metrics` omite las claves None (== proyección Mongo que omite ausentes)."""
    where, params = "", ()
    if instrumento:
        where = "WHERE ticker ILIKE %s ESCAPE '\\'"
        params = (_ilike_param(instrumento),)
    rows = _q(
        f"SELECT ticker, book, {', '.join(c for c, _ in _METRIC_COLS)} "
        f"FROM mercado.market_snapshot {where}",
        params,
    )

    docs: list[dict] = []
    for r in rows:
        metrics = {}
        for col, key in _METRIC_COLS:
            v = _f(r[col])
            if v is not None:
                metrics[key] = v
        doc = {"instrumento": r["ticker"], "metrics": metrics}
        if r.get("book") is not None:
            doc["book"] = r["book"]
        docs.append(doc)

    # ── Enriquecimiento TC breakeven (tasa fija nativa + CER ya fijados) ──
    fijados = _bonos_cer_fijados()
    cond = "curva = 'tasa_fija'"
    cparams: list = []
    if fijados:
        cond = "(curva = 'tasa_fija' OR ticker = ANY(%s))"
        cparams.append(list(fijados))
    flujo_por_ticker: dict[str, float] = {}
    for c in _q(
        f"SELECT ticker, flujo_vencimiento FROM mercado.curvas WHERE {cond}",
        tuple(cparams),
    ):
        fv = _f(c["flujo_vencimiento"])
        if fv and fv > 0:
            flujo_por_ticker[c["ticker"]] = fv

    if flujo_por_ticker:
        from api.services.macro import get_ultimo_mep  # lazy: evita ciclo
        mep_doc = get_ultimo_mep()
        mep = mep_doc.get("mep") if mep_doc else None
        for d in docs:
            fv = flujo_por_ticker.get(d.get("instrumento") or "")
            if fv is None:
                continue
            m = d["metrics"]
            m["flujo_vencimiento"] = fv
            if mep:
                m["tc_breakeven"] = _tc_breakeven(m.get("last_price"), fv, mep)

    return docs


# ─────────────────────────────────────────────────────────────────────────────
# listar_curva — curva enriquecida (mercado.curvas + market_snapshot)
# ─────────────────────────────────────────────────────────────────────────────


def _fetch_curva_docs(curva: str, fijados: set[str]) -> list[dict]:
    """Equivalente SQL de las queries a Trading.Curvas por curva en listar_curva."""
    if curva == "cer":
        sql = ("SELECT ticker, ticker_corto, tipo, fecha_vencimiento, fecha_emision, "
               "cupon_anual, cer_emision FROM mercado.curvas WHERE curva = 'cer'")
        params: list = []
        if fijados:
            sql += " AND ticker <> ALL(%s)"
            params.append(list(fijados))
        return _q(sql, tuple(params))
    if curva == "tasa_fija":
        if fijados:
            return _q(
                "SELECT ticker, ticker_corto, tipo, curva, fecha_vencimiento, "
                "fecha_emision, flujo_vencimiento FROM mercado.curvas "
                "WHERE curva = 'tasa_fija' OR (curva = 'cer' AND ticker = ANY(%s))",
                (list(fijados),),
            )
        return _q(
            "SELECT ticker, ticker_corto, tipo, curva, fecha_vencimiento, "
            "fecha_emision, flujo_vencimiento FROM mercado.curvas WHERE curva = 'tasa_fija'",
        )
    if _es_curva_on(curva):
        if curva == "on":
            return _q(
                "SELECT ticker, ticker_corto, tipo, fecha_vencimiento, fecha_emision, "
                "curva, emisor, moneda_flujo FROM mercado.curvas WHERE curva LIKE 'on%'")
        return _q(
            "SELECT ticker, ticker_corto, tipo, fecha_vencimiento, fecha_emision, "
            "curva, emisor, moneda_flujo FROM mercado.curvas WHERE curva = %s", (curva,))
    return _q(
        "SELECT ticker, ticker_corto, tipo, fecha_vencimiento, fecha_emision "
        "FROM mercado.curvas WHERE curva = %s", (curva,))


def _market_maps(tickers: list[str]) -> tuple[dict, dict]:
    """(enrich_map, vol_map) desde mercado.market_snapshot — UNA query por todos
    los tickers (== el find $in de Mongo). enrich_map solo para last_price>0."""
    enrich_map: dict[str, dict] = {}
    vol_map: dict[str, dict] = {}
    if not tickers:
        return enrich_map, vol_map
    for r in _q(
        "SELECT ticker, updated_at, last_price, tea, tem, paridad, duration, "
        "mod_duration, convexity, total_nominals FROM mercado.market_snapshot "
        "WHERE ticker = ANY(%s)",
        (tickers,),
    ):
        # total_money MUERTO (REGLA #1 19/6): el motor no lo escribe → no se migra.
        # El volumen vivo es total_nominals.
        vol_map[r["ticker"]] = {"total_nominals": _f(r["total_nominals"]) or 0}
        if (_f(r["last_price"]) or 0) > 0:
            ts = r.get("updated_at")
            enrich_map[r["ticker"]] = {
                "price":        _f(r["last_price"]),
                "TEA":          _f(r["tea"]),
                "TEM":          _f(r["tem"]),
                "paridad":      _f(r["paridad"]),
                "duration":     _f(r["duration"]),
                "mod_duration": _f(r["mod_duration"]),
                "convexity":    _f(r["convexity"]),
                # Mongo updated_at es NAIVE; SQL timestamptz es aware → emparejar
                # el formato isoformat dropeando el tz (mismo criterio que market_sql).
                "ts": ts.replace(tzinfo=None) if isinstance(ts, datetime) else ts,
            }
    return enrich_map, vol_map


def listar_curva(
    curva: str,
    ordenar_por: str = "vencimiento",
    vencimiento_min_meses: float | None = None,
    vencimiento_max_meses: float | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Espejo SQL de renta_fija.listar_curva. Misma lógica de ensamblado/orden;
    solo cambian las fuentes (mercado.curvas + market_snapshot en vez de Trading.*).
    Reasignación CER↔tasa_fija idéntica (reusa `_bonos_cer_fijados`, Mongo)."""
    if curva not in _CURVAS_VALIDAS and not _es_curva_on(curva):
        return []
    if ordenar_por not in _ORDENES_VALIDOS:
        ordenar_por = "vencimiento"

    fijados_tickers = _bonos_cer_fijados() if curva in ("cer", "tasa_fija") else set()
    curva_docs = _fetch_curva_docs(curva, fijados_tickers)
    if not curva_docs:
        return []

    ahora = datetime.now(UTC)
    filtrados: list[dict] = []
    for d in curva_docs:
        vto_raw = d.get("fecha_vencimiento")
        if not vto_raw:
            continue
        try:
            if isinstance(vto_raw, datetime):
                vto = vto_raw if vto_raw.tzinfo else vto_raw.replace(tzinfo=UTC)
            else:  # date o str
                vto = datetime.fromisoformat(str(vto_raw)[:10]).replace(tzinfo=UTC)
        except Exception:
            continue
        meses = round((vto - ahora).days / 30.44, 1)
        if vencimiento_min_meses is not None and meses < vencimiento_min_meses:
            continue
        if vencimiento_max_meses is not None and meses > vencimiento_max_meses:
            continue
        d["_meses"] = meses
        filtrados.append(d)

    if not filtrados:
        return []

    tickers = [d["ticker"] for d in filtrados]
    enrich_map, vol_map = _market_maps(tickers)

    mep_actual: float | None = None
    if curva == "tasa_fija":
        from api.services.macro import get_ultimo_mep  # lazy: evita ciclo
        mep_doc = get_ultimo_mep()
        mep_raw = mep_doc.get("mep") if mep_doc else None
        if mep_raw and mep_raw > 0:
            mep_actual = float(mep_raw)

    out: list[dict] = []
    for d in filtrados:
        enrich = enrich_map.get(d["ticker"], {})
        vol = vol_map.get(d["ticker"], {})
        ts_last = enrich.get("ts")
        entry = {
            "ticker": d["ticker"],
            "ticker_corto": d.get("ticker_corto"),
            "tipo": d.get("tipo"),
            "fecha_vencimiento": str(d.get("fecha_vencimiento"))[:10] if d.get("fecha_vencimiento") else None,
            "fecha_emision": str(d.get("fecha_emision"))[:10] if d.get("fecha_emision") else None,
            "meses_al_vto": d["_meses"],
            "ultimo_precio": enrich.get("price"),
            "tea": enrich.get("TEA"),
            "tem": enrich.get("TEM"),
            "paridad": enrich.get("paridad"),
            "duration": enrich.get("duration"),
            "mod_duration": enrich.get("mod_duration"),
            "convexity": enrich.get("convexity"),
            "total_nominals_dia": vol.get("total_nominals"),
            "ts_ultimo_trade": ts_last.isoformat() if isinstance(ts_last, datetime) else ts_last,
        }
        if d["ticker"] in fijados_tickers:
            entry["cer_fijado"] = True
        if curva == "tasa_fija":
            entry["tc_breakeven"] = _tc_breakeven(
                enrich.get("price"), _f(d.get("flujo_vencimiento")), mep_actual,
            )
        if curva == "cer":
            cupon = d.get("cupon_anual")
            entry["is_zero_coupon"] = (cupon is None) or (float(cupon) == 0.0)
            cer_em = d.get("cer_emision")
            if cer_em:
                entry["cer_emision"] = float(cer_em)
        if _es_curva_on(str(d.get("curva", ""))):
            entry["emisor"] = d.get("emisor")
            entry["sector"] = d.get("curva")
            entry["moneda"] = d.get("moneda_flujo")
        out.append(entry)

    if ordenar_por == "vencimiento":
        out.sort(key=lambda x: x.get("fecha_vencimiento") or "9999")
    elif ordenar_por == "volumen_dia":
        # Mongo ordenaba por total_money (muerto=0 → no-op). SQL-native: por el
        # volumen VIVO (nominales). REGLA #1 19/6: no arrastrar la lógica rota.
        out.sort(key=lambda x: -(x.get("total_nominals_dia") or 0))
    elif ordenar_por == "tea":
        out.sort(key=lambda x: (x.get("tea") is None, x.get("tea") or 0))
    elif ordenar_por == "duration":
        out.sort(key=lambda x: (x.get("duration") is None, x.get("duration") or 0))

    if limit and limit > 0:
        out = out[:limit]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# get_historico_curva — serie diaria por ticker (mercado.snapshots_cierre_hist)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=60)
def get_historico_curva(curva: str) -> list:
    """Espejo SQL de renta_fija.get_historico_curva. Cierre persistido desde
    mercado.snapshots_cierre_hist; si hoy aún no tiene cierre, agrega una fila
    por ticker desde mercado.market_snapshot (mismo live-fallback que Mongo)."""
    out: list = []
    fechas_persistidas: set[str] = set()
    for r in _q(
        "SELECT to_char(fecha, 'YYYY-MM-DD') AS fecha, ticker_corto, ticker, tipo, "
        "ultimo_precio, tea, tem, duration, paridad "
        "FROM mercado.snapshots_cierre_hist WHERE curva = %s ORDER BY fecha, ticker",
        (curva,),
    ):
        fechas_persistidas.add(r.get("fecha") or "")
        out.append({
            "fecha":    r.get("fecha"),
            "ticker":   r.get("ticker_corto") or r.get("ticker"),
            "tipo":     r.get("tipo"),
            "price":    _f(r.get("ultimo_precio")),
            "TEA":      _f(r.get("tea")),
            "TEM":      _f(r.get("tem")),
            "duration": _f(r.get("duration")),
            "paridad":  _f(r.get("paridad")),
        })

    hoy_str = date.today().isoformat()
    if hoy_str not in fechas_persistidas:
        meta = {
            d["ticker"]: d
            for d in _q(
                "SELECT ticker, ticker_corto, tipo FROM mercado.curvas WHERE curva = %s",
                (curva,),
            )
        }
        if meta:
            for r in _q(
                "SELECT ticker, last_price, tea, tem, duration, paridad "
                "FROM mercado.market_snapshot "
                "WHERE ticker = ANY(%s) AND last_price > 0",
                (list(meta.keys()),),
            ):
                m = meta.get(r.get("ticker"), {})
                out.append({
                    "fecha":    hoy_str,
                    "ticker":   m.get("ticker_corto") or r.get("ticker"),
                    "tipo":     m.get("tipo"),
                    "price":    _f(r.get("last_price")),
                    "TEA":      _f(r.get("tea")),
                    "TEM":      _f(r.get("tem")),
                    "duration": _f(r.get("duration")),
                    "paridad":  _f(r.get("paridad")),
                })
    return out
