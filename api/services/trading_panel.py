"""Panel de Trading intradía de CEDEARs (vista /trading) — orquestación.

Une los services de mercado ya existentes con la lógica pura de
`trading_systems.py` y persiste la watchlist por usuario en SQL.

NO pasa por el MCP (eso es el asistente). Reusa los mismos services que el
Scanner: `scanner_sql.get_cedears_scanner / get_pivot_points / get_quant_stats`
(todos `@cached`) + `get_cedears_intraday` (tape de hoy). Cero infra nueva salvo
la tabla `manager.trading_watchlist`. Ver [[project_vista_trading]].

Tiers de costo (los caches viven en los services):
  - scanner: 1 sola llamada para TODO el universo (@cached ttl=2) → se filtra a
    la watchlist en memoria.
  - pivots / quant_stats: 1 por ticker, @cached ttl=60 (cambian 1 vez/rueda).
  - intraday (velas): 1 por ticker, sin cache (tape live) — la watchlist es chica.
"""
from __future__ import annotations

import logging

from psycopg.rows import dict_row

from api.services import scanner_sql as svc
from api.services.trading_systems import derivar_campos, evaluar_sistemas
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Watchlist por defecto si el usuario todavía no guardó la suya.
DEFAULT_WATCHLIST: tuple[str, ...] = ("RKLB", "SNDK")

_WATCHLIST_TABLE = "manager.trading_watchlist"


# ── panel ───────────────────────────────────────────────────────────────────

def get_panel(*, tickers: list[str]) -> dict:
    """Computa el panel para una watchlist de ticker_corto.

    Returns:
        {
          generado_en: ISO,
          ccl: {value, vs_1d_pct, ts},
          rows: [{...campos, sistemas: {S1..S5: {estado, lado, nota}}}, ...]
        }
    """
    pedidos = [t.strip().upper() for t in (tickers or []) if t and t.strip()]
    ccl = svc.get_ccl_live()

    # 1 sola llamada al scanner (todo el universo) → índice por ticker_corto
    try:
        universo = svc.get_cedears_scanner()
    except Exception as e:
        logger.warning("trading_panel: scanner falló (%s)", e)
        universo = []
    por_ticker = {
        str(r.get("ticker_corto", "")).upper(): r for r in universo if r.get("ticker_corto")
    }

    rows: list[dict] = []
    for tk in pedidos:
        sr = por_ticker.get(tk)
        if sr is None:
            rows.append({"ticker": tk, "error": "no está en el universo de CEDEARs activos"})
            continue
        pivots = _safe(svc.get_pivot_points, tk, "pivots")
        stats = _safe(svc.get_quant_stats, tk, "quant_stats")
        candles = _safe(svc.get_cedears_intraday, tk, "intraday") or []
        campos = derivar_campos(sr, pivots, stats, candles)
        campos["sistemas"] = evaluar_sistemas(campos)
        rows.append(campos)

    # mantiene el orden pedido por el usuario
    return {"generado_en": _now_iso(), "ccl": ccl, "rows": rows}


def _safe(fn, ticker: str, label: str):
    """Llama un service por ticker tolerando fallos (un papel roto no mata el panel)."""
    try:
        return fn(ticker=ticker)
    except Exception as e:
        logger.debug("trading_panel: %s falló para %s (%s)", label, ticker, e)
        return None


def _now_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


# ── watchlist por usuario (SQL) ───────────────────────────────────────────────

def get_watchlist(*, email: str) -> list[str]:
    """Watchlist persistida del usuario; DEFAULT_WATCHLIST si todavía no guardó."""
    em = (email or "").lower().strip()
    if not em:
        return list(DEFAULT_WATCHLIST)
    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT tickers FROM {_WATCHLIST_TABLE} WHERE email = %s", (em,)
            )
            row = cur.fetchone()
    except Exception as e:
        logger.warning("trading_panel: get_watchlist SQL falló (%s) → default", e)
        return list(DEFAULT_WATCHLIST)
    if row and row.get("tickers"):
        return list(row["tickers"])
    return list(DEFAULT_WATCHLIST)


def set_watchlist(*, email: str, tickers: list[str]) -> list[str]:
    """Guarda (upsert) la watchlist del usuario. Normaliza, deduplica y limita a 40."""
    em = (email or "").lower().strip()
    if not em:
        raise ValueError("email vacío")
    limpios: list[str] = []
    for t in tickers or []:
        tk = str(t).strip().upper()
        if tk and tk not in limpios:
            limpios.append(tk)
    limpios = limpios[:40]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {_WATCHLIST_TABLE} (email, tickers, updated_at) "
            "VALUES (%s, %s, now()) "
            "ON CONFLICT (email) DO UPDATE SET tickers = EXCLUDED.tickers, "
            "updated_at = now()",
            (em, limpios),
        )
        conn.commit()
    return limpios
