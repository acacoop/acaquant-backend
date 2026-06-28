"""fair_value.py — service del módulo Fair Value relativo intra-curva.

Tres entrypoints:

  - get_fair_value_live(curva): live intra-rueda. Usa β del último cierre
    (SQL mercado.fit_params) + TEAs vivas de mercado.market_snapshot.metrics.TEA.
    Re-computa tea_teorica, residuo_bps y z_estatico con esos TEAs vivos.
    z_temporal lo trae del último cierre persistido (no se recalcula
    intra-día — la media/desvío de 30d se mantiene fija hasta el próximo cron).

  - get_fair_value_cierre(curva, fecha): lee directamente SQL
    mercado.fair_value_residuos del cierre solicitado (default = último disponible).

  - get_fair_value_historico_bono(ticker, dias): serie de residuos diarios
    del bono — alimenta el modal de drill-down en la UI.

Patrón split-persist consistente con z-score forwards: el agregado lento
(β del cierre, media/desvío 30d) vive en SQL (mercado.{fit_params,fair_value_residuos});
el componente rápido (TEA live) viene de market_snapshot y se mezcla en este
service. Real-time genuino, sin lag entre ticks del live.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from api.cache import cached
from api.db import get_db_trading
from core.postgres import get_pool

_CURVAS_VALIDAS = ("tasa_fija", "cer")


def _f(v) -> float | None:
    return float(v) if v is not None else None


def _norm_residuo(r: dict) -> dict:
    """Fila SQL de fair_value_residuos → mismo shape que el doc Mongo legacy
    (ts_cierre string ISO, numéricos float)."""
    return {
        "ts_cierre":    r["ts_cierre"].isoformat() if r["ts_cierre"] else None,
        "curva":        r["curva"],
        "ticker":       r["ticker"],
        "ticker_corto": r["ticker_corto"],
        "duration":     _f(r["duration"]),
        "tea_obs":      _f(r["tea_obs"]),
        "tea_teorica":  _f(r["tea_teorica"]),
        "residuo_bps":  _f(r["residuo_bps"]),
        "z_estatico":   _f(r["z_estatico"]),
        "z_temporal":   _f(r["z_temporal"]),
        "n_obs":        r["n_obs"],
        "en_universo":  r["en_universo"],
    }


def _fitparams(curva: str, fecha: str | None = None) -> dict | None:
    """β del cierre desde SQL mercado.fit_params. fecha=None → último cierre.
    Devuelve dict con ts_cierre string ISO + numéricos float (shape legacy)."""
    from psycopg.rows import dict_row

    sql = (
        "SELECT ts_cierre, beta0, beta1, beta2, r2, n_bonos_universo, sigma_dia_bps "
        "FROM mercado.fit_params WHERE curva = %s"
    )
    params: list = [curva]
    if fecha is not None:
        sql += " AND ts_cierre = %s"
        params.append(date.fromisoformat(fecha))
    else:
        sql += " ORDER BY ts_cierre DESC LIMIT 1"
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        return None
    return {
        "ts_cierre":        row["ts_cierre"].isoformat(),
        "beta0":            _f(row["beta0"]),
        "beta1":            _f(row["beta1"]),
        "beta2":            _f(row["beta2"]),
        "r2":               _f(row["r2"]),
        "n_bonos_universo": row["n_bonos_universo"],
        "sigma_dia_bps":    _f(row["sigma_dia_bps"]),
    }


def _residuos_por_ticker(curva: str, ts_cierre: str) -> dict[str, dict]:
    """Mapa ticker → fila de fair_value_residuos del cierre indicado.

    Usado para traer z_temporal y campos persistidos al armar la respuesta
    live (el numerador es nuevo, los stats son los del cierre).
    """
    from psycopg.rows import dict_row

    out: dict[str, dict] = {}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT ts_cierre, curva, ticker, ticker_corto, duration, tea_obs, "
            "tea_teorica, residuo_bps, z_estatico, z_temporal, n_obs, en_universo "
            "FROM mercado.fair_value_residuos WHERE curva = %s AND ts_cierre = %s",
            (curva, date.fromisoformat(ts_cierre)),
        )
        for r in cur.fetchall():
            out[r["ticker"]] = _norm_residuo(r)
    return out


def _tickers_curva(db, curva: str) -> list[dict]:
    return list(db["Curvas"].find(
        {"curva": curva},
        {"_id": 0, "ticker": 1, "ticker_corto": 1},
    ))


def _live_metricas(db, tickers: list[str]) -> dict[str, dict]:
    """TEA, duration vivas por ticker — SQL-only (mercado.market_snapshot)."""
    from core.market_snapshot import snapshot_docs
    out: dict[str, dict] = {}
    for ticker, doc in snapshot_docs(tickers).items():
        mt = doc["metrics"]
        out[ticker] = {
            "tea": mt.get("TEA"),
            "duration": mt.get("duration"),
            "updated_at": doc["updated_at"],
        }
    return out


@cached(ttl=30)
def get_fair_value_live(curva: str) -> dict:
    """Recompone fair value con β del cierre + TEAs vivas.

    Output:
        {
          curva, ts_cierre_beta, beta0, beta1, beta2, r2, sigma_dia_bps,
          updated_at: <ts del más reciente MarketSnapshot leído>,
          bonos: [
            {ticker, ticker_corto, duration, tea_obs, tea_teorica,
             residuo_bps, z_estatico, z_temporal, n_obs, en_universo}
          ]
        }
    Si no hay FitParams persistidos para la curva → {error}.
    """
    if curva not in _CURVAS_VALIDAS:
        return {"error": f"curva inválida (válidas: {list(_CURVAS_VALIDAS)})"}

    fp = _fitparams(curva)
    if not fp:
        return {"error": "sin FitParams — corré jobs.fair_value primero"}

    ts_cierre = fp["ts_cierre"]
    beta0 = float(fp["beta0"])
    beta1 = float(fp["beta1"])
    beta2 = float(fp["beta2"])
    sigma = float(fp.get("sigma_dia_bps") or 0.0)

    cierre_residuos = _residuos_por_ticker(curva, ts_cierre)
    db = get_db_trading()
    tickers_meta = _tickers_curva(db, curva)
    tickers = [d["ticker"] for d in tickers_meta if d.get("ticker")]
    live = _live_metricas(db, tickers)

    bonos: list[dict] = []
    last_updated = None
    for meta in tickers_meta:
        ticker = meta["ticker"]
        m = live.get(ticker)
        if not m:
            continue
        tea = m.get("tea")
        dur = m.get("duration")
        if tea is None or dur is None or dur <= 0:
            continue
        tea_teorica = beta0 + beta1 * dur + beta2 * dur * dur
        residuo_bps = (float(tea) - tea_teorica) * 10000
        z_estatico = residuo_bps / sigma if sigma > 1e-9 else None

        cierre = cierre_residuos.get(ticker, {})
        bonos.append({
            "ticker":       ticker,
            "ticker_corto": meta.get("ticker_corto"),
            "duration":     float(dur),
            "tea_obs":      float(tea),
            "tea_teorica":  tea_teorica,
            "residuo_bps":  residuo_bps,
            "z_estatico":   z_estatico,
            # z_temporal viene del cierre — no se recompute intra-día.
            "z_temporal":   cierre.get("z_temporal"),
            "n_obs":        cierre.get("n_obs"),
            "en_universo":  bool(cierre.get("en_universo")),
        })
        ts_upd = m.get("updated_at")
        if ts_upd and (last_updated is None or ts_upd > last_updated):
            last_updated = ts_upd

    bonos.sort(key=lambda b: b["duration"])
    return {
        "curva":          curva,
        "ts_cierre_beta": ts_cierre,
        "beta0":          beta0,
        "beta1":          beta1,
        "beta2":          beta2,
        "r2":             float(fp.get("r2") or 0.0),
        "sigma_dia_bps":  sigma,
        "n_bonos_universo": int(fp.get("n_bonos_universo") or 0),
        "updated_at":     last_updated.isoformat() if isinstance(last_updated, datetime) else None,
        "bonos":          bonos,
    }


@cached(ttl=300)
def get_fair_value_cierre(curva: str, fecha: str | None = None) -> dict:
    """Snapshot persistido del cierre. Si fecha=None usa el último disponible."""
    if curva not in _CURVAS_VALIDAS:
        return {"error": f"curva inválida (válidas: {list(_CURVAS_VALIDAS)})"}

    if fecha is None:
        fp = _fitparams(curva)
        if not fp:
            return {"error": "sin FitParams para esa curva"}
        fecha = fp["ts_cierre"]
    else:
        fp = _fitparams(curva, fecha)
        if not fp:
            return {"error": f"sin FitParams para {curva}@{fecha}"}

    cierre = _residuos_por_ticker(curva, fecha)
    bonos = sorted(cierre.values(), key=lambda b: (b["duration"] is None, b["duration"]))
    return {
        "curva":          curva,
        "ts_cierre":      fecha,
        "beta0":          float(fp["beta0"]),
        "beta1":          float(fp["beta1"]),
        "beta2":          float(fp["beta2"]),
        "r2":             float(fp.get("r2") or 0.0),
        "sigma_dia_bps":  float(fp.get("sigma_dia_bps") or 0.0),
        "n_bonos_universo": int(fp.get("n_bonos_universo") or 0),
        "bonos":          bonos,
    }


@cached(ttl=300)
def get_fair_value_historico_bono(ticker: str, dias: int = 60) -> dict:
    """Serie de residuos diarios del bono. Para el modal de drill-down."""
    from psycopg.rows import dict_row

    if dias <= 0 or dias > 365:
        dias = 60
    desde = datetime.now(UTC).date() - timedelta(days=int(dias * 1.7))
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT ts_cierre, residuo_bps, z_temporal, z_estatico, tea_obs, "
            "tea_teorica, duration, curva FROM mercado.fair_value_residuos "
            "WHERE ticker = %s AND ts_cierre >= %s ORDER BY ts_cierre ASC",
            (ticker, desde),
        )
        rows = cur.fetchall()
    # Limitamos a `dias` cierres en la cola (los últimos):
    rows = rows[-dias:]
    if not rows:
        return {"ticker": ticker, "dias": dias, "serie": []}
    curva = rows[-1].get("curva")
    serie = [
        {
            "fecha":       r["ts_cierre"].isoformat() if r["ts_cierre"] else None,
            "residuo_bps": _f(r["residuo_bps"]),
            "z_temporal":  _f(r["z_temporal"]),
            "z_estatico":  _f(r["z_estatico"]),
            "tea_obs":     _f(r["tea_obs"]),
            "tea_teorica": _f(r["tea_teorica"]),
            "duration":    _f(r["duration"]),
        }
        for r in rows
    ]
    return {"ticker": ticker, "curva": curva, "dias": dias, "serie": serie}
