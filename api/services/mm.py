"""MM Workstation — service de backtest.

Port en Python puro de `runBacktest` de docs/mm_workstation.jsx, manteniendo
las MISMAS reglas para que REPLAY (frontend, JS) y BACKTEST (backend, Python)
sean equivalentes:

  - mid = SMA(últimos MID_WINDOW prints).
  - skewOffset = -(inv/cap) * intensity * spread/2 si auto_skew y cap > 0.
  - bid = mid - spread/2 + skewOffset, offer = mid + spread/2 + skewOffset.
  - Si trade.side == BUY y offer <= trade.price → fill al offer (vendemos).
  - Si trade.side == SELL y bid >= trade.price → fill al bid (compramos).
  - fillSize = min(quote_size, trade.size). Sólo se respeta inv_cap (no se
    inicia un fill que rompería el cap).
  - PnL final = cash + inv * mid_final.

El backtest histórico recorre N días: por cada combinación de spread del sweep
(7 valores), se corre el sim 1 vez por día y se devuelven estadísticas
agregadas (PnL medio, std, win rate, Sharpe simple, max drawdown).

Cada tick usa lookups en Mongo (Trading.TimeSales) — con cache 60s server-side
8 users que abran la misma sesión generan trabajo del backend solo del primero.
"""
from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime, time, timedelta
from typing import Any

from api.cache import cached
from core.mongo import get_mongo_client_read

logger = logging.getLogger("api.services.mm")

DB_TRADING = "Trading"
COL_TIMESALES = "TimeSales"

# ─────────────────────────────────────────────────────────────────────────────
# Constantes — alineadas con docs/mm_workstation.jsx (NO tocar sin sincronizar
# REPLAY del frontend; sino los resultados divergen).
# ─────────────────────────────────────────────────────────────────────────────

MID_WINDOW = 20             # rolling SMA para el mid
DEFAULT_DUST_THRESHOLD = 500  # filtro de dust trades (size < N se ignora)

# Sweep default — 7 spreads cubriendo de tight a ancho.
DEFAULT_SWEEP_SPREADS = (0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.30)


# ─────────────────────────────────────────────────────────────────────────────
# Trades fetcher
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=120)
def fetch_trades_dia(
    *,
    instrumento_full: str,
    fecha: str,  # "YYYY-MM-DD"
    dust: int = DEFAULT_DUST_THRESHOLD,
) -> list[dict[str, Any]]:
    """Trae los trades de UN día desde Trading.TimeSales para un ticker
    completo (ej "MERV - XMEV - AL30D - 24hs"). Filtra dust por default.

    Devuelve [{timestamp, price, size, side}, ...] ordenado ascendente
    por timestamp. Lo consumen tanto REPLAY (que se lo sirve al frontend
    via /api/mm/trades) como BACKTEST (server-side).

    Nota motor_rofex graba timestamps naive ART rotulados como UTC en
    Mongo (-3h vs UTC real, ver MOTOR_TS_OFFSET en operativa_mep.py).
    Para filtrar por fecha calendario "del día", el rango en Mongo es
    [fecha 00:00, fecha 23:59:59] como naive ART — Mongo no sabe que
    son ART, los compara como UTC y matchea correcto.
    """
    inicio = datetime.combine(_parse_date(fecha), time.min)
    fin = datetime.combine(_parse_date(fecha), time.max)
    db = get_mongo_client_read()[DB_TRADING]
    cursor = (
        db[COL_TIMESALES]
        .find(
            {
                "ticker": instrumento_full,
                "timestamp": {"$gte": inicio, "$lte": fin},
                "size": {"$gte": dust},
            },
            {"_id": 0, "timestamp": 1, "price": 1, "size": 1, "side": 1},
        )
        .sort("timestamp", 1)
    )
    return [
        {
            "timestamp": doc["timestamp"].isoformat() if isinstance(doc.get("timestamp"), datetime) else doc.get("timestamp"),
            "price": float(doc.get("price") or 0),
            "size": int(doc.get("size") or 0),
            "side": str(doc.get("side") or "").upper(),
        }
        for doc in cursor
    ]


def _parse_date(s: str):
    return datetime.fromisoformat(s).date()


def fechas_con_actividad(
    *,
    instrumento_full: str,
    desde: str,
    hasta: str,
    min_trades: int = 100,
) -> list[str]:
    """Lista de fechas (YYYY-MM-DD) con al menos `min_trades` prints en
    el rango. Sirve para el backtest: solo iteramos días con sesión real.
    """
    inicio = datetime.combine(_parse_date(desde), time.min)
    fin = datetime.combine(_parse_date(hasta), time.max)
    db = get_mongo_client_read()[DB_TRADING]
    cursor = db[COL_TIMESALES].aggregate([
        {"$match": {
            "ticker": instrumento_full,
            "timestamp": {"$gte": inicio, "$lte": fin},
            "size": {"$gte": DEFAULT_DUST_THRESHOLD},
        }},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            "n": {"$sum": 1},
        }},
        {"$match": {"n": {"$gte": min_trades}}},
        {"$sort": {"_id": 1}},
    ])
    return [row["_id"] for row in cursor]


# ─────────────────────────────────────────────────────────────────────────────
# Backtest core — port directo de runBacktest() de mm_workstation.jsx
# ─────────────────────────────────────────────────────────────────────────────


def run_backtest_dia(
    trades: list[dict[str, Any]],
    *,
    spread: float,
    quote_size: float,
    skew_intensity: float = 1.0,
    auto_skew: bool = True,
    inv_cap: float = 200_000,
) -> dict[str, Any]:
    """1 corrida de 1 día. Replica runBacktest() de la spec JS.

    Cada trade viene como {timestamp, price, size, side="BUY"|"SELL"}.
    """
    if not trades:
        return _empty_result(spread)

    cash = 0.0
    inventory = 0.0
    spread_pnl = 0.0
    fills_buy = 0
    fills_sell = 0
    max_inv = 0.0
    min_inv = 0.0

    mid_window: list[float] = []
    last_mid = float(trades[0]["price"])

    # Track de equity para max_drawdown (mark-to-market en cada tick).
    peak_equity = 0.0
    max_dd = 0.0

    for trade in trades:
        price = float(trade["price"])
        size = float(trade["size"])
        side = trade["side"]  # "BUY" | "SELL" | otro (skip)

        mid_window.append(price)
        if len(mid_window) > MID_WINDOW:
            mid_window.pop(0)
        mid = sum(mid_window) / len(mid_window)

        if auto_skew and inv_cap > 0:
            skew_offset = -(inventory / inv_cap) * skew_intensity * spread * 0.5
        else:
            skew_offset = 0.0

        bid = mid - spread / 2.0 + skew_offset
        offer = mid + spread / 2.0 + skew_offset

        if side == "BUY" and offer <= price:
            would_be_inv = inventory - quote_size
            if would_be_inv >= -inv_cap:
                fill_size = min(quote_size, size)
                inventory -= fill_size
                cash += offer * fill_size
                spread_pnl += (offer - mid) * fill_size
                fills_sell += 1
        elif side == "SELL" and bid >= price:
            would_be_inv = inventory + quote_size
            if would_be_inv <= inv_cap:
                fill_size = min(quote_size, size)
                inventory += fill_size
                cash -= bid * fill_size
                spread_pnl += (mid - bid) * fill_size
                fills_buy += 1

        if inventory > max_inv:
            max_inv = inventory
        if inventory < min_inv:
            min_inv = inventory

        # Equity actual = cash + inventario MtM. Drawdown vs peak.
        equity_now = cash + inventory * mid
        if equity_now > peak_equity:
            peak_equity = equity_now
        else:
            dd = peak_equity - equity_now
            if dd > max_dd:
                max_dd = dd

        last_mid = mid

    total_pnl = cash + inventory * last_mid
    return {
        "spread":      spread,
        "total_pnl":   total_pnl,
        "spread_pnl":  spread_pnl,
        "inv_pnl":     total_pnl - spread_pnl,
        "total_fills": fills_buy + fills_sell,
        "fills_buy":   fills_buy,
        "fills_sell":  fills_sell,
        "max_inv":     max_inv,
        "min_inv":     min_inv,
        "final_inv":   inventory,
        "max_dd":      max_dd,
    }


def _empty_result(spread: float) -> dict[str, Any]:
    return {
        "spread":      spread,
        "total_pnl":   0.0,
        "spread_pnl":  0.0,
        "inv_pnl":     0.0,
        "total_fills": 0,
        "fills_buy":   0,
        "fills_sell":  0,
        "max_inv":     0.0,
        "min_inv":     0.0,
        "final_inv":   0.0,
        "max_dd":      0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Sweep multi-día
# ─────────────────────────────────────────────────────────────────────────────


def run_backtest_sweep(
    *,
    instrumento_full: str,
    desde: str,
    hasta: str,
    quote_size: float,
    skew_intensity: float = 1.0,
    auto_skew: bool = True,
    inv_cap: float = 200_000,
    spreads: tuple[float, ...] = DEFAULT_SWEEP_SPREADS,
) -> dict[str, Any]:
    """Para cada spread × día, corre run_backtest_dia. Devuelve por spread:
    pnl medio, desvío, win rate, Sharpe, max DD, fills medio. Más detalle
    por día para que el frontend pueda dibujar.
    """
    fechas = fechas_con_actividad(
        instrumento_full=instrumento_full, desde=desde, hasta=hasta,
    )
    if not fechas:
        return {
            "instrumento_full": instrumento_full,
            "desde":            desde,
            "hasta":            hasta,
            "fechas":           [],
            "params":           {
                "quote_size":     quote_size,
                "skew_intensity": skew_intensity,
                "auto_skew":      auto_skew,
                "inv_cap":        inv_cap,
                "spreads":        list(spreads),
            },
            "por_spread":       [],
            "por_dia":          [],
        }

    # Trades por fecha — los pre-cargamos para no re-fetchearlos por
    # cada spread (7 spreads × 30 días = 210 fetches sin esto).
    trades_por_fecha: dict[str, list[dict[str, Any]]] = {}
    for f in fechas:
        trades_por_fecha[f] = fetch_trades_dia(
            instrumento_full=instrumento_full, fecha=f,
        )

    # Por_dia: lista plana de {fecha, spread, ...resultado}.
    por_dia: list[dict[str, Any]] = []
    for f in fechas:
        trades_f = trades_por_fecha[f]
        for spread in spreads:
            r = run_backtest_dia(
                trades_f,
                spread=spread,
                quote_size=quote_size,
                skew_intensity=skew_intensity,
                auto_skew=auto_skew,
                inv_cap=inv_cap,
            )
            por_dia.append({"fecha": f, **r})

    # Por_spread: agregamos las N corridas (1 por día) en stats.
    por_spread: list[dict[str, Any]] = []
    for spread in spreads:
        runs = [r for r in por_dia if r["spread"] == spread]
        pnls = [r["total_pnl"] for r in runs]
        if not pnls:
            continue
        mean_pnl = statistics.mean(pnls)
        std_pnl = statistics.stdev(pnls) if len(pnls) > 1 else 0.0
        win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
        sharpe = (mean_pnl / std_pnl) if std_pnl > 1e-9 else 0.0
        max_dd_avg = statistics.mean([r["max_dd"] for r in runs])
        fills_avg = statistics.mean([r["total_fills"] for r in runs])
        por_spread.append({
            "spread":     spread,
            "n_dias":     len(runs),
            "pnl_mean":   mean_pnl,
            "pnl_std":    std_pnl,
            "pnl_min":    min(pnls),
            "pnl_max":    max(pnls),
            "win_rate":   win_rate,
            "sharpe":     sharpe,
            "max_dd_avg": max_dd_avg,
            "fills_avg":  fills_avg,
        })

    return {
        "instrumento_full": instrumento_full,
        "desde":            desde,
        "hasta":            hasta,
        "fechas":           fechas,
        "params":           {
            "quote_size":     quote_size,
            "skew_intensity": skew_intensity,
            "auto_skew":      auto_skew,
            "inv_cap":        inv_cap,
            "spreads":        list(spreads),
        },
        "por_spread":       por_spread,
        "por_dia":          por_dia,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helper para el endpoint público — convertir desde "ticker_corto" o
# "instrumento_full" indistintamente.
# ─────────────────────────────────────────────────────────────────────────────


def resolver_instrumento_full(instrumento: str) -> str:
    """Acepta ticker corto ('AL30D') o full ('MERV - XMEV - AL30D - 24hs')
    y devuelve siempre el full. Para corto, busca en Trading.Curvas."""
    if " - " in instrumento:
        return instrumento  # ya es full
    db = get_mongo_client_read()["Trading"]["Curvas"]
    doc = db.find_one({"ticker_corto": instrumento}, {"_id": 0, "curva": 1})
    if doc and doc.get("curva"):
        return doc["curva"]
    # Fallback intuitivo: bonos cotizan en 24hs por default.
    return f"MERV - XMEV - {instrumento} - 24hs"


def fechas_disponibles(instrumento_full: str, dias_atras: int = 30) -> list[str]:
    """Devuelve las fechas con actividad (>=100 trades) en los últimos N
    días. Utilizado por el dropdown de día del REPLAY."""
    hasta = datetime.now(UTC).date().isoformat()
    desde = (datetime.now(UTC).date() - timedelta(days=dias_atras)).isoformat()
    return fechas_con_actividad(
        instrumento_full=instrumento_full, desde=desde, hasta=hasta,
    )
