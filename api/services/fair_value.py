"""fair_value.py — service del módulo Fair Value relativo intra-curva.

Tres entrypoints:

  - get_fair_value_live(curva): live intra-rueda. Usa β del último cierre
    (Trading.FitParams) + TEAs vivas de Trading.MarketSnapshot.metrics.TEA.
    Re-computa tea_teorica, residuo_bps y z_estatico con esos TEAs vivos.
    z_temporal lo trae del último cierre persistido (no se recalcula
    intra-día — la media/desvío de 30d se mantiene fija hasta el próximo cron).

  - get_fair_value_cierre(curva, fecha): lee directamente Trading.FairValueResiduos
    del cierre solicitado (default = último disponible).

  - get_fair_value_historico_bono(ticker, dias): serie de residuos diarios
    del bono — alimenta el modal de drill-down en la UI.

Patrón split-persist consistente con z-score forwards: el agregado lento
(β del cierre, media/desvío 30d) vive en Mongo; el componente rápido (TEA
live) viene de MarketSnapshot y se mezcla en este service. Real-time genuino,
sin lag entre ticks del live.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.cache import cached
from api.db import get_db_trading

_CURVAS_VALIDAS = ("tasa_fija", "cer")


def _ultimo_fitparams(db, curva: str) -> dict | None:
    return db["FitParams"].find_one(
        {"curva": curva},
        {"_id": 0},
        sort=[("ts_cierre", -1)],
    )


def _ultimo_residuos_por_ticker(db, curva: str, ts_cierre: str) -> dict[str, dict]:
    """Mapa ticker → doc de FairValueResiduos del cierre indicado.

    Usado para traer z_temporal y campos persistidos al armar la respuesta
    live (el numerador es nuevo, los stats son los del cierre).
    """
    out: dict[str, dict] = {}
    for d in db["FairValueResiduos"].find(
        {"curva": curva, "ts_cierre": ts_cierre},
        {"_id": 0},
    ):
        out[d["ticker"]] = d
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

    db = get_db_trading()
    fp = _ultimo_fitparams(db, curva)
    if not fp:
        return {"error": "sin FitParams — corré jobs.fair_value primero"}

    ts_cierre = fp["ts_cierre"]
    beta0 = float(fp["beta0"])
    beta1 = float(fp["beta1"])
    beta2 = float(fp["beta2"])
    sigma = float(fp.get("sigma_dia_bps") or 0.0)

    cierre_residuos = _ultimo_residuos_por_ticker(db, curva, ts_cierre)
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
    db = get_db_trading()

    if fecha is None:
        fp = _ultimo_fitparams(db, curva)
        if not fp:
            return {"error": "sin FitParams para esa curva"}
        fecha = fp["ts_cierre"]
    else:
        fp = db["FitParams"].find_one(
            {"curva": curva, "ts_cierre": fecha}, {"_id": 0},
        )
        if not fp:
            return {"error": f"sin FitParams para {curva}@{fecha}"}

    bonos = list(db["FairValueResiduos"]
                 .find({"curva": curva, "ts_cierre": fecha}, {"_id": 0})
                 .sort("duration", 1))
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
    db = get_db_trading()
    if dias <= 0 or dias > 365:
        dias = 60
    desde = (datetime.now(UTC).date() - timedelta(days=int(dias * 1.7))).isoformat()
    rows = list(
        db["FairValueResiduos"]
        .find(
            {"ticker": ticker, "ts_cierre": {"$gte": desde}},
            {"_id": 0, "ts_cierre": 1, "residuo_bps": 1, "z_temporal": 1,
             "z_estatico": 1, "tea_obs": 1, "tea_teorica": 1, "duration": 1,
             "curva": 1},
        )
        .sort("ts_cierre", 1)
    )
    # Limitamos a `dias` cierres en la cola (los últimos):
    rows = rows[-dias:]
    if not rows:
        return {"ticker": ticker, "dias": dias, "serie": []}
    curva = rows[-1].get("curva")
    serie = [
        {
            "fecha":      r["ts_cierre"],
            "residuo_bps": r.get("residuo_bps"),
            "z_temporal":  r.get("z_temporal"),
            "z_estatico":  r.get("z_estatico"),
            "tea_obs":     r.get("tea_obs"),
            "tea_teorica": r.get("tea_teorica"),
            "duration":    r.get("duration"),
        }
        for r in rows
    ]
    return {"ticker": ticker, "curva": curva, "dias": dias, "serie": serie}
