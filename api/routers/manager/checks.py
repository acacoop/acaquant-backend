"""GET /api/manager/checks/* — validaciones de consistencia (SQL)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.services import portfolio_sql
from api.services.assets_sql import assets_rows
from core import curvas_sql

router = APIRouter()


@router.get("/checks/tasa-fija")
def check_tasa_fija():
    """Estado de instrumentos tasa_fija en AuM."""
    curvas_tf = curvas_sql.por_curva("tasa_fija")   # mercado.curvas (SQL)
    if not curvas_tf:
        return {"snapshot": None, "ok": 0, "sin_posicion": 0, "sin_assets": 0, "instrumentos": []}

    t2u: dict[str, list] = {}
    for a in assets_rows(["TICKER"]):   # SQL portafolio.assets
        t2u.setdefault(a["TICKER"], []).append(a["unidad"])

    fm, con_pos = portfolio_sql.unidades_con_posicion()   # última foto del AuM

    rows = []
    for c in sorted(curvas_tf, key=lambda x: x.get("ticker_corto", "")):
        tc = c["ticker_corto"]
        uns = t2u.get(tc, [])
        estado = "ok" if any(u in con_pos for u in uns) else ("sin_assets" if not uns else "sin_posicion")
        rows.append({"ticker": tc, "estado": estado})

    return {"snapshot": str(fm)[:10] if fm else None,
            "ok": sum(1 for r in rows if r["estado"] == "ok"),
            "sin_posicion": sum(1 for r in rows if r["estado"] == "sin_posicion"),
            "sin_assets": sum(1 for r in rows if r["estado"] == "sin_assets"),
            "instrumentos": rows}


@router.get("/checks/tickers-curvas")
def tickers_curvas():
    """Lista de ticker_corto disponibles en mercado.curvas (SQL)."""
    return sorted({
        d["ticker_corto"] for d in curvas_sql.cargar_todos()
        if d.get("ticker_corto")
    })


@router.get("/checks/debug-soberano")
def debug_soberano(
    ticker_corto: str = Query(..., description="Ticker corto del bono soberano (ej. GD30D)"),
):
    """Reproduce paso a paso el cálculo del branch soberano de engines/curvas
    (instrumento, precio USD, flujos al settlement, cashflow del XIRR,
    TEA/duration/paridad). Lógica en api/services/debug_derivados.py."""
    from api.services import debug_derivados
    try:
        return debug_derivados.debug_soberano(ticker_corto)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/checks/breakevens-debug")
def check_breakevens_debug():
    """Desglose paso a paso del BE por par Lecap/Boncap ↔ CER (Buscar Objetivo
    + Fisher clásico). Lógica en api/services/debug_derivados.py."""
    from api.services import debug_derivados
    return debug_derivados.breakevens_debug()


@router.get("/checks/debug-tna-futuros")
def check_debug_tna_futuros():
    """TNA lineal vs TEA compuesta por outright DLR, paso a paso.
    Lógica en api/services/debug_derivados.py."""
    from api.services import debug_derivados
    return debug_derivados.debug_tna_futuros()


@router.get("/checks/debug-curva-tea")
def check_debug_curva_tea(ticker: str):
    """Debug paso-a-paso del cálculo de TEA/TNA/Duration de un ticker.

    Replica la lógica de engines/curvas.calcular_campos() devolviendo
    todos los inputs intermedios (instrumento, settlement, CER, MEP/TC,
    flujos futuros, cashflow del XIRR) + el resultado recalculado vs
    el persistido en TimeSales.

    Soporta tasa_fija, cer, soberanos, dolar_linked y ONs (on / on_<sector>).
    Para ONs sin TEA, explica el motivo (XIRR no convergió / fuera de rango).
    """
    from api.services.debug_curva import debug_calculo_tea
    return debug_calculo_tea(ticker)


@router.get("/checks/debug-pivot")
def check_debug_pivot(
    ticker: str = Query(..., description="Ticker de Trading.PreciosAcciones (ej. NVDA)"),
):
    """Debug paso-a-paso de los pivot points de un ticker.

    Para los 4 timeframes (diario/semanal/mensual/anual) devuelve la ventana
    de fechas consultada, TODAS las velas usadas, de qué vela sale cada
    H/L/C, la fórmula Floor Trader con los números reales y los niveles
    resultantes. Lee `Trading.PreciosAcciones`.
    """
    from quant.pivot_points import debug_4_timeframes
    return debug_4_timeframes(ticker.strip().upper())
