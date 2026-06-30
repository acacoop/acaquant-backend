"""Router /api/trading — vista TRADING (panel intradía de CEDEARs).

Módulo `trading` (admin-only, ver core/roles.py + api/main.py). Consume la vista
acaquant-web /trading: por cada ticker de la watchlist del usuario calcula los
campos derivados y resuelve los 5 sistemas como semáforo. Toda la lógica vive en
`api/services/trading_panel.py` (+ `trading_systems.py` puro). Ver
[[project_vista_trading]].
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import trading_panel as svc

router = APIRouter(prefix="/api/trading", tags=["Trading"])


class WatchlistBody(BaseModel):
    tickers: list[str] = Field(default_factory=list)


@router.get("/panel")
def panel(
    tickers: str | None = Query(None, description="CSV de ticker_corto; vacío = watchlist guardada"),
    email: str = Depends(get_user_email),
):
    """Panel de los 5 sistemas por ticker. Sin `tickers` usa la watchlist del usuario.

    Returns:
        {generado_en, ccl, rows: [{...campos, sistemas: {S1..S5}}]}
    """
    if tickers:
        lista = [t.strip() for t in tickers.split(",") if t.strip()]
    else:
        lista = svc.get_watchlist(email=email)
    return svc.get_panel(tickers=lista)


@router.get("/watchlist")
def get_watchlist(email: str = Depends(get_user_email)):
    """Watchlist persistida del usuario (ticker_corto)."""
    return {"tickers": svc.get_watchlist(email=email)}


@router.put("/watchlist")
def set_watchlist(body: WatchlistBody, email: str = Depends(get_user_email)):
    """Guarda la watchlist del usuario. Devuelve la lista normalizada."""
    return {"tickers": svc.set_watchlist(email=email, tickers=body.tickers)}
