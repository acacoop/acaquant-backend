"""Router /api/operar — soporte para la vista "Operar Dashboard".

Endpoints utilitarios que la vista del dashboard consume para mostrar
order book y datos live de instrumentos arbitrarios (no solo dólar MEP).
El envío real de órdenes sigue yendo por `/api/ordenes` (no se duplica).

Endpoints:
  GET /api/operar/order-book?ticker=X  → top 5 niveles bid/ask + meta.
    Si el motor ya lo suscribe → 200 con book.
    Si no, y el ticker existe en pyRofex → registra en AdhocSubscriptions
    y devuelve 202 (motor lo suscribe al próximo poll, ~5s).

Gate RBAC se aplica en api/main.py vía `_OPERAR`.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from core.adhoc_subscriptions import bump_last_used, subscribe
from core.mongo import get_mongo_client_read
from api.services.order_book import get_order_book

router = APIRouter(prefix="/api/operar", tags=["operar"])


def _existe_en_pyrofex(ticker_full: str) -> bool:
    """¿pyRofex conoce este ticker? Lookup en Manager.PyRofexInstruments.

    Evita registrar basura en AdhocSubscriptions (typos, tickers viejos).
    Si discovery_pyrofex nunca corrió, esta validación no aplica y se
    permite el subscribe optimista — el motor lo va a ignorar igual si
    pyRofex no lo reconoce.
    """
    db = get_mongo_client_read()["Manager"]
    if "PyRofexInstruments" not in db.list_collection_names():
        return True  # discovery aún no corrió; permitir.
    doc = db["PyRofexInstruments"].find_one(
        {"instruments.ticker": ticker_full},
        {"_id": 1},
    )
    return doc is not None


@router.get("/order-book")
def get_book(
    ticker: str = Query(..., description="Ticker corto (AL30) o full ROFEX"),
    plazo: str = Query("24hs", description="CI | 24hs | 48hs (ignorado si ticker es full)"),
):
    """Top 5 niveles de un ticker arbitrario.

    Si MarketSnapshot ya lo tiene → 200 con book.
    Si no, y el ticker existe en pyRofex → 202 (suscribiendo) y se persiste
    en Trading.AdhocSubscriptions; el motor lo levanta al próximo ciclo
    (~5s) y aparece en MarketSnapshot.
    """
    book = get_order_book(ticker, plazo=plazo)
    if book is not None:
        # Refresca TTL si era adhoc (no rompe nada si no estaba ahí).
        try:
            bump_last_used(book.get("ticker", ""))
        except Exception:
            pass
        return book

    # No está en MarketSnapshot. Resolver el ticker FULL para registrar
    # en AdhocSubscriptions (necesitamos el full para que pyRofex lo
    # suscriba). Si el user nos pasó full, usamos eso directo; si pasó
    # corto, construimos `MERV - XMEV - {corto} - {plazo}`.
    if " - " in ticker:
        ticker_full = ticker.strip()
    else:
        ticker_full = f"MERV - XMEV - {ticker.strip()} - {plazo}"

    if not _existe_en_pyrofex(ticker_full):
        raise HTTPException(
            404,
            f"Ticker {ticker_full!r} desconocido en pyRofex. "
            "Verificá el símbolo o corré scripts.discovery_pyrofex.",
        )

    res = subscribe(ticker_full)
    if not res["ok"] and res.get("reason") == "cap":
        raise HTTPException(
            429,
            f"Cap de {res.get('cap')} suscripciones adhoc alcanzado. "
            f"Esperá a que expire alguna (TTL 7d) o pedile a admin que limpie.",
        )

    return JSONResponse(
        status_code=202,
        content={
            "status":       "subscribing",
            "ticker":       ticker_full,
            "created":      bool(res.get("created", False)),
            "active_count": res.get("active_count"),
            "message":      "Motor suscribiendo. Reintentá en ~5s.",
        },
    )
