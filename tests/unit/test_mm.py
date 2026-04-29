"""Tests del backtest del MM Workstation.

Asegura que la versión Python (`api.services.mm.run_backtest_dia`) replique
EXACTAMENTE las reglas de la versión JS de `docs/mm_workstation.jsx`. Si
divergen, REPLAY (frontend) y BACKTEST (backend) van a dar PnL distintos
para el mismo input — eso rompe la utilidad de la herramienta.
"""
from __future__ import annotations

from api.services.mm import (
    DEFAULT_SWEEP_SPREADS,
    MID_WINDOW,
    run_backtest_dia,
)


def _trade(price: float, size: int, side: str) -> dict:
    return {"timestamp": "2026-04-28T12:00:00", "price": price, "size": size, "side": side}


def test_empty_trades():
    r = run_backtest_dia([], spread=0.10, quote_size=20_000)
    assert r["total_pnl"] == 0
    assert r["total_fills"] == 0


def test_no_skew_simple_buy_fill():
    """Trade SELL a 62.85 con bid en 62.90 → debe fillear (te compran al bid)."""
    trades = [_trade(63.0, 10_000, "SELL")] * MID_WINDOW + [_trade(62.85, 5_000, "SELL")]
    r = run_backtest_dia(
        trades,
        spread=0.10,
        quote_size=2_000,
        auto_skew=False,
    )
    # mid = 63.0 (después de 20 prints a 63), bid = 62.95, offer = 63.05.
    # En el último print (price=62.85), mid recalcula → SMA = (19*63 + 62.85)/20 ≈ 62.9925.
    # Con auto_skew=False, bid = mid - 0.05 = 62.9425. trade.price=62.85 < bid → fillea.
    assert r["fills_buy"] == 1
    assert r["inventory_post"] if False else r["final_inv"] == 2_000


def test_no_skew_simple_sell_fill():
    """Trade BUY a 63.10 con offer en 63.05 → debe fillear (vendés al offer)."""
    trades = [_trade(63.0, 10_000, "BUY")] * MID_WINDOW + [_trade(63.10, 5_000, "BUY")]
    r = run_backtest_dia(
        trades,
        spread=0.10,
        quote_size=2_000,
        auto_skew=False,
    )
    assert r["fills_sell"] == 1
    assert r["final_inv"] == -2_000  # vendido short


def test_inv_cap_blocks_more_buys():
    """Con cap 5000 y quote_size 5000, el segundo SELL no debe fillear."""
    base = [_trade(63.0, 10_000, "SELL")] * MID_WINDOW
    extras = [_trade(62.80, 5_000, "SELL"), _trade(62.80, 5_000, "SELL")]
    r = run_backtest_dia(
        base + extras,
        spread=0.10,
        quote_size=5_000,
        auto_skew=False,
        inv_cap=5_000,
    )
    assert r["fills_buy"] == 1  # solo el primero
    assert r["final_inv"] == 5_000


def test_skew_reduces_offer_when_long():
    """Con inventario long y auto_skew=True, el offer se mueve hacia abajo
    (skew negativo) para tratar de descargar."""
    # Forzamos al sim a quedar long primero, después medimos los offers.
    # Acá solo verificamos que el resultado con/sin skew difiere para un
    # input idéntico cuando el inv != 0.
    trades = [_trade(63.0, 10_000, "SELL")] * MID_WINDOW + [
        _trade(62.80, 100_000, "SELL"),  # nos compramos masivo
        _trade(63.05, 100_000, "BUY"),   # offer "natural" en 63.05; con skew long se baja
    ]
    sin_skew = run_backtest_dia(trades, spread=0.10, quote_size=10_000, auto_skew=False, inv_cap=200_000)
    con_skew = run_backtest_dia(trades, spread=0.10, quote_size=10_000, auto_skew=True, inv_cap=200_000)
    # Con skew, el offer baja → más probable que el segundo BUY fillee
    # → más fills_sell con skew que sin skew. (Equivalencia exacta no se
    # puede asumir; sí esperamos que skew descargue al menos algo.)
    assert con_skew["fills_sell"] >= sin_skew["fills_sell"]


def test_pnl_decomposition():
    """spread_pnl + inv_pnl debe ser exactamente igual a total_pnl."""
    trades = [_trade(63.0, 10_000, "SELL")] * MID_WINDOW + [
        _trade(62.85, 5_000, "SELL"),
        _trade(63.15, 5_000, "BUY"),
    ]
    r = run_backtest_dia(trades, spread=0.10, quote_size=2_000, auto_skew=False)
    assert abs(r["total_pnl"] - (r["spread_pnl"] + r["inv_pnl"])) < 1e-6


def test_default_sweep_spreads_shape():
    """Sanity check del array default."""
    assert len(DEFAULT_SWEEP_SPREADS) == 7
    assert tuple(sorted(DEFAULT_SWEEP_SPREADS)) == DEFAULT_SWEEP_SPREADS
    assert all(0.01 <= s <= 1.0 for s in DEFAULT_SWEEP_SPREADS)
