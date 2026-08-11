"""Router /api/operar — soporte para la vista "Operar Dashboard".

Endpoints utilitarios que la vista del dashboard consume para mostrar
order book y datos live de instrumentos arbitrarios (no solo dólar MEP).
El envío real de órdenes sigue yendo por `/api/ordenes` (no se duplica).

Endpoints:
  GET  /api/operar/order-book?ticker=X  → top 5 niveles bid/ask + meta.
    Si el motor ya lo suscribe → 200 con book.
    Si no, y el ticker existe en pyRofex → registra en AdhocSubscriptions
    y devuelve 202 (motor lo suscribe al próximo poll, ~5s).
  POST /api/operar/bracket               → manda orden LIMIT de entrada
    y persiste un bracket; el motor_ordenes dispara la salida automática
    cuando la entrada llega a FILLED.
  GET  /api/operar/brackets/dia          → brackets activos/históricos del día.

Gate RBAC se aplica en api/main.py vía `_OPERAR`.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services._grupos_scope import scope_cuentas, verificar_account
from api.services._idempotencia import ejecutar_idempotente
from api.services.ordenes import send_order, ticker_existe
from api.services.order_book import get_order_book
from core.adhoc_subscriptions import bump_last_used, subscribe
from core.brackets import create_bracket, ensure_indexes
from core.brackets import list_dia as list_brackets_dia

logger = logging.getLogger("api.operar")

router = APIRouter(prefix="/api/operar", tags=["operar"])


def _existe_en_pyrofex(ticker_full: str) -> bool:
    """¿pyRofex conoce este ticker? Evita registrar basura en AdhocSubscriptions
    (typos, tickers viejos).

    La resolución vive en el service (`ordenes.ticker_existe`): universo LIVE del
    broker primero, `manager.pyrofex_instruments` de fallback. Antes miraba SOLO
    la tabla SQL, que la escribe un one-shot manual y puede estar vieja — un
    ticker dado de alta después de la última corrida daba 404 aunque existiera.
    """
    return ticker_existe(ticker_full)


def _tiene_puntas(book: dict) -> bool:
    """True si el libro trae al menos una punta (bid u offer)."""
    b = book.get("book") or {}
    return bool(b.get("bids")) or bool(b.get("offers"))


# Cuánto puede tener una fila sin refrescarse antes de considerarla abandonada.
# Por debajo de esto, un libro vacío es un libro vacío DE VERDAD (mercado
# cerrado, papel sin oferta) y hay que mostrarlo; por encima, es una fila que el
# motor dejó de tocar y corresponde re-suscribir.
_FRESCURA_S = 90


def _segundos_desde(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds()


@router.get("/order-book")
def get_book(
    ticker: str = Query(..., description="Ticker corto (AL30) o full ROFEX"),
    plazo: str = Query("24hs", description="CI | 24hs | 48hs (ignorado si ticker es full)"),
):
    """Top 5 niveles de un ticker arbitrario.

    Tres desenlaces, y la diferencia importa:

    - **200 con puntas** — el caso normal.
    - **200 con `sin_puntas: true`** — la fila está FRESCA pero el libro está
      vacío: mercado cerrado, o papel sin oferta. NO es un problema: se
      devuelve igual, con el último precio, para que la pantalla lo diga y el
      usuario pueda mandar una orden límite. Antes esto caía al camino de
      suscripción y el cliente recibía 202 para siempre — pantalla en blanco
      con un spinner eterno y ninguna explicación (reporte del user
      2026-07-22: "elegís un ticker y no trae nada").
    - **202 suscribiendo** — no hay fila, o la que hay está abandonada (el
      motor dejó de refrescarla). El motor la levanta en ~5s. El payload dice
      hace cuánto que no se toca, para que el cliente pueda distinguir "recién
      pedido" de "el motor no está corriendo".
    """
    book = get_order_book(ticker, plazo=plazo)
    if book is not None:
        edad = _segundos_desde(book.get("updated_at"))
        fresca = edad is not None and edad <= _FRESCURA_S
        # La FRESCURA manda, tenga puntas o no. Antes acá decía
        # `if _tiene_puntas(book) or fresca`, y ese `or` servía la fila apenas
        # tuviera puntas SIN mirar de cuándo eran: con el motor sin refrescar el
        # papel, la pantalla pintaba un libro viejo como si fuera de ahora y nunca
        # se llegaba al camino de re-suscripción de abajo. Caso real (2026-08-07):
        # RKLB mostraba bid 9130 / ask 9170 de una fila de **16 días** mientras el
        # último precio real era 10860. Un libro rancio es PEOR que uno vacío: uno
        # avisa, el otro te deja mandar una orden límite contra puntas fantasma.
        if fresca:
            # Refresca TTL si era adhoc (no rompe nada si no estaba ahí).
            try:
                bump_last_used(book.get("ticker", ""))
            except Exception:
                pass
            if not _tiene_puntas(book):
                book = {**book, "sin_puntas": True,
                        "motivo": "el libro está vacío (mercado cerrado o sin "
                                  "oferta) — el último precio sí es real"}
            return book
    # Sin fila, o fila abandonada: caer al camino de suscripción para que el
    # motor la vuelva a levantar (no devolver un DOM muerto).
    edad_fila = _segundos_desde(book.get("updated_at")) if book else None

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
            f"Ticker {ticker_full!r} desconocido en pyRofex. Verificá el símbolo.",
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
            # hace cuánto que la fila no se refresca (None = nunca existió).
            # Es LO ÚNICO que distingue "recién lo pedí" de "el motor está
            # caído y esto no va a llegar nunca": sin este dato el cliente
            # sigue reintentando a ciegas.
            "fila_edad_s":  round(edad_fila) if edad_fila is not None else None,
            # Último precio conocido de la fila vieja. NO son puntas operables — es
            # solo para que la pantalla diga "lo último que vi fue X, hace Y" en vez
            # de quedarse en blanco mientras el motor levanta el papel.
            "last_price":   ((book or {}).get("metrics") or {}).get("last_price"),
            "message":      "Motor suscribiendo. Reintentá en ~5s.",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Brackets — entrada LIMIT + salida automática al fill
# ─────────────────────────────────────────────────────────────────────────────


class BracketIn(BaseModel):
    ticker: str = Field(..., description="Ticker full ROFEX")
    side: Literal["BUY", "SELL"] = Field(..., description="Side de la ENTRADA")
    size: int = Field(..., gt=0)
    price_entry: float = Field(..., gt=0, description="Precio LIMIT de la entrada")
    price_exit: float = Field(..., gt=0, description="Precio LIMIT de la salida automática")
    tif: Literal["DAY", "IOC", "FOK", "GTC"] = "DAY"
    account: str = Field(..., description="Cuenta operativa")
    client_order_id: str | None = Field(
        None, description="Clave de idempotencia opcional (anti doble bracket).",
    )


@router.post("/bracket", status_code=201)
def crear_bracket(
    data: BracketIn,
    email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> dict[str, Any]:
    """Manda la orden de ENTRADA al broker; si la acepta, persiste el
    bracket. El motor_ordenes, al recibir el ER de FILL de la entrada,
    dispara la SALIDA con side opuesto al `price_exit` definido.

    Si la entrada se rechaza al envío, no se persiste bracket y se
    devuelve el error tal cual lo devolvió send_order.
    """
    verificar_account(data.account, scope)

    def _do():
        try:
            ensure_indexes()
        except Exception as e:
            logger.warning("brackets: ensure_indexes falló (continúo): %s", e)

        # 1. Mandar la entrada al broker.
        res = send_order(
            ticker=data.ticker,
            side=data.side,
            size=data.size,
            order_type="LIMIT",
            price=data.price_entry,
            tif=data.tif,
            account=data.account,
            actor_email=email,
        )
        if not res.get("ok"):
            raise HTTPException(
                status_code=400,
                detail=res.get("error", "broker rechazó la entrada"),
            )

        entry_cl_ord_id = res["cl_ord_id"]
        entry_proprietary = res.get("proprietary")

        # 2. Persistir el bracket. Si esto falla, la entrada YA está mandada —
        # el frontend va a verla como una orden normal en la tabla del día,
        # solo que sin salida automática (queda manual). No es bloqueante.
        try:
            doc = create_bracket(
                entry_cl_ord_id=entry_cl_ord_id,
                entry_proprietary=entry_proprietary,
                ticker=data.ticker,
                side_entry=data.side,
                price_entry=data.price_entry,
                size=data.size,
                price_exit=data.price_exit,
                tif=data.tif,
                account=data.account,
                actor_email=email,
            )
        except Exception as e:
            logger.exception("brackets: persist falló — entrada %s queda manual", entry_cl_ord_id)
            return {
                "ok":              True,
                "warning":         f"entrada mandada pero bracket no persistido: {e}",
                "entry_cl_ord_id": entry_cl_ord_id,
            }

        return {
            "ok":              True,
            "entry_cl_ord_id": entry_cl_ord_id,
            "bracket":         {
                "status":      doc["status"],
                "ticker":      doc["ticker"],
                "side_entry":  doc["side_entry"],
                "price_entry": doc["price_entry"],
                "price_exit":  doc["price_exit"],
                "size":        doc["size"],
            },
        }

    return ejecutar_idempotente(data.client_order_id, _do)


@router.get("/brackets/dia")
def brackets_dia(
    account: str | None = Query(None),
    _email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> list[dict[str, Any]]:
    """Lista de brackets — todos los estados, ordenados por created_at desc."""
    verificar_account(account, scope)
    return list_brackets_dia(account=account)
