"""api/services/renta_fija_sql.py — renta fija LIVE leyendo Postgres (mercado.*).

Read-side de MERCADO para renta fija. Qué lee cada función:

  - get_renta_fija      ← mercado.market_snapshot           (snapshot live + TC BE)
  - listar_curva        ← mercado.curvas + market_snapshot  (curva enriquecida)
  - get_historico_curva ← mercado.snapshots_cierre_hist (+ fallback live de hoy)
  - get_historico_trades← mercado.timesales                 (tape del día)
  - _bonos_cer_fijados  ← macro.series_macro (CER) + mercado.curvas + mercado.dias_habiles

Se reexportan de `api/services/renta_fija.py` los helpers compartidos y las
funciones de derivados: `_CURVAS_VALIDAS`, `resolver_ticker_exacto`,
`calendario_ons`, `get_retorno_total_data`.

El MEP live sale de `macro.get_ultimo_mep` (snapshot del motor de dólar, TTL 5s).

`total_money` (volumen $ del día) está MUERTO: el motor dejó de escribirlo
(engines/valores.py::_calcular_metricas). No se arrastra — `total_money_dia` no
sale en el output y el orden 'volumen_dia' ordena por el volumen VIVO
(`total_nominals_dia`), no por un 0 fantasma.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

from api.cache import cached
from api.services._sql import _f, _q

# Helpers PUROS + passthroughs del path Mongo (drop-in del selector).
# `_bonos_cer_fijados` NO se importa: acá es SQL-native (ver abajo).
from api.services.renta_fija import (  # noqa: F401  (reexport intencional)
    _CURVAS_VALIDAS,
    _ORDENES_VALIDOS,
    _es_curva_on,
    _tc_breakeven,
    calendario_ons,
    get_retorno_total_data,
    resolver_ticker_exacto,
)

logger = logging.getLogger(__name__)


def _ilike_param(instrumento: str) -> str:
    """Substring case-insensitive (== regex re.escape de Mongo): escapa los
    comodines LIKO (% _ \\) para que el input se trate literal."""
    esc = instrumento.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


@cached(ttl=15)
def get_historico_trades(instrumento: str | None = None) -> list:
    """Trades de HOY desde SQL (mercado.timesales — dual-write del motor bajo SNAPSHOT_SQL).
    Mismo shape que el path Mongo (instrumento, timestamp, price, size, side, money), de
    mayor a menor ts. SOLO el día (cutoff = inicio de hoy UTC, idéntico a Mongo) — el tape
    no muestra histórico. Sin los enriquecidos TEA/TEM/duration: el tape no los usa."""
    # `ts` se guarda naive en hora ART (igual que Mongo) → cutoff naive ART de hoy.
    art_hoy = (datetime.now(UTC) - timedelta(hours=3)).replace(
        tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    where = ["ts >= %s"]
    params: list = [art_hoy]
    if instrumento:
        exacto = resolver_ticker_exacto(instrumento)
        if exacto is None:
            return []
        where.append("ticker = %s")
        params.append(exacto)
    rows = _q(
        f"SELECT ticker AS instrumento, ts AS timestamp, price, size, side, money "
        f"FROM mercado.timesales WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT 10000",
        tuple(params),
    )
    for r in rows:
        r["price"] = _f(r["price"])
        r["size"] = _f(r["size"])
        r["money"] = _f(r["money"])
    return rows


def _dias_habiles_ordenados() -> list[str]:
    """Días hábiles ('YYYY-MM-DD' asc) desde mercado.dias_habiles (SQL-only).
    Delega en core.calendario — fuente única, sin Mongo."""
    from core.calendario import dias_habiles_ordenados
    return dias_habiles_ordenados()


@cached(ttl=600)
def _bonos_cer_fijados() -> set[str]:
    """Tickers CER cuyo CER de liquidación del VTO (T-10 hábiles) ya fue publicado
    por el BCRA → comportan tasa fija. CER (macro.series_macro) y la curva CER
    (mercado.curvas) salen de SQL; los días hábiles también (mercado.dias_habiles,
    SQL-first + fallback Mongo vía `_dias_habiles_ordenados`). La lógica T-10 la pone `fecha_cer_liquidacion`
    (puro, reusado). Strings 'YYYY-MM-DD' para que la comparación lexicográfica == la
    del path Mongo. Fallback set() ante error.

    TTL=600s (subido de 30s 2026-06-27): el set cambia 1×/día (cuando BCRA
    publica el CER del día, job 22 UTC) pero costaba ~345ms en frío (scan de
    Trading.DiasHabiles en Mongo) y se recalculaba cada 30s. Era el cuello de
    botella de get_renta_fija. 10 min de staleness es inocuo para este set."""
    from engines.curvas import fecha_cer_liquidacion
    try:
        cmax = _q("SELECT to_char(max(fecha), 'YYYY-MM-DD') AS f "
                  "FROM macro.series_macro WHERE serie = 'CER'")
        max_cer = cmax[0]["f"] if cmax else None
        if not max_cer:
            return set()
        dias_habiles = _dias_habiles_ordenados()  # SQL-first, fallback Mongo
        fijados: set[str] = set()
        for r in _q("SELECT ticker, to_char(fecha_vencimiento, 'YYYY-MM-DD') AS vto "
                    "FROM mercado.curvas WHERE curva = 'cer'"):
            vto = (r.get("vto") or "")[:10]
            if not vto:
                continue
            fecha_liq = fecha_cer_liquidacion(dias_habiles, vto, n=10)
            if fecha_liq and fecha_liq <= max_cer:
                fijados.add(r["ticker"])
        return fijados
    except Exception:
        logger.exception("_bonos_cer_fijados (SQL) falló — devuelvo set vacío (fallback)")
        return set()


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

    def _leer_snapshot() -> list[dict]:
        # `book` (order book JSONB depth-5) NO se trae: la vista /renta-fija no
        # lo consume y traerlo para los ~301 instrumentos costaba ~120ms +
        # payload pesado. El order book vive en api/services/order_book.py.
        return _q(
            f"SELECT ticker, {', '.join(c for c, _ in _METRIC_COLS)} "
            f"FROM mercado.market_snapshot {where}",
            params,
        )

    def _leer_flujos() -> dict[str, float]:
        # TC breakeven: tasa fija nativa + CER ya fijados.
        fijados = _bonos_cer_fijados()
        cond = "curva = 'tasa_fija'"
        cparams: list = []
        if fijados:
            cond = "(curva = 'tasa_fija' OR ticker = ANY(%s))"
            cparams.append(list(fijados))
        out: dict[str, float] = {}
        for c in _q(
            f"SELECT ticker, flujo_vencimiento FROM mercado.curvas WHERE {cond}",
            tuple(cparams),
        ):
            fv = _f(c["flujo_vencimiento"])
            if fv and fv > 0:
                out[c["ticker"]] = fv
        return out

    def _leer_mep():
        from api.services.macro import get_ultimo_mep  # lazy: evita ciclo
        return get_ultimo_mep()

    # Las 3 lecturas son independientes → en paralelo (telemetría 2026-08-05:
    # 719ms avg en frío; en serie el endpoint pagaba la suma de round-trips).
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_rows = ex.submit(_leer_snapshot)
        f_flujos = ex.submit(_leer_flujos)
        f_mep = ex.submit(_leer_mep)
        rows = f_rows.result()
        flujo_por_ticker = f_flujos.result()
        mep_doc = f_mep.result()

    docs: list[dict] = []
    for r in rows:
        metrics = {}
        for col, key in _METRIC_COLS:
            v = _f(r[col])
            if v is not None:
                metrics[key] = v
        docs.append({"instrumento": r["ticker"], "metrics": metrics})

    if flujo_por_ticker:
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
    if curva in ("tamar", "dual"):
        # Por el EJE, no por el string viejo: los duales están guardados con
        # `curva='cer'` o `curva='tamar'` y sin este branch la curva sale vacía
        # (era el "la curva de DUALES no existe en el backend" de la vista).
        return _q(
            "SELECT ticker, ticker_corto, tipo, curva, fecha_vencimiento, "
            "fecha_emision, flujo_vencimiento FROM mercado.curvas WHERE ajuste = %s",
            (curva,))
    if _es_curva_on(curva):
        if curva == "on":
            return _q(
                "SELECT ticker, ticker_corto, tipo, fecha_vencimiento, fecha_emision, "
                "curva, emisor, moneda_flujo FROM mercado.curvas WHERE curva LIKE 'on%%'")
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


@cached(ttl=10)
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
