"""GET /api/manager/checks/* — validaciones de consistencia sobre Mongo."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.services.assets_sql import assets_rows
from core import curvas_sql
from core.postgres import get_pool

router = APIRouter()


@router.get("/checks/debug-comercial")
def check_debug_comercial(
    operador: str | None = Query(None, description="operador_email a auditar"),
    segmento: str | None = Query(None, description="nivel_1 a auditar"),
    moneda: str = Query("ARS", description="ARS | USD"),
):
    """Auditoría del Informe comercial: desglose por cuenta (# ops, volumen,
    arancel) + totales + ticket promedio, para un operador o un segmento."""
    from api.services.comercial_sql import debug_comercial
    return debug_comercial(operador=operador, segmento=segmento, moneda=moneda)


_DEBUG_OBSOLETO = {
    "deshabilitado_post_decomiso": True,
    "nota": (
        "Check obsoleto: debuggeaba el enriquecimiento por-trade en Trading.TimeSales "
        "(Mongo, dropeada). El enriquecimiento (TEA/duration/paridad) ahora vive LIVE en "
        "mercado.market_snapshot, no por-trade. Usá /api/manager/checks/debug-curva-tea "
        "(SQL) o /api/manager/status para frescura."
    ),
}


@router.get("/checks/curvas-pendientes")
def check_curvas_pendientes():
    """OBSOLETO post-decomiso Mongo — ver _DEBUG_OBSOLETO."""
    return {"total": 0, "ok": True, "tickers": [], **_DEBUG_OBSOLETO}


@router.get("/checks/forwards")
def check_forwards():
    """OBSOLETO post-decomiso Mongo — ver _DEBUG_OBSOLETO."""
    return _DEBUG_OBSOLETO


@router.get("/checks/cer")
def check_cer():
    """OBSOLETO post-decomiso Mongo — ver _DEBUG_OBSOLETO."""
    return {"cer_reciente": None, "dias_habiles": 0, "instrumentos": [], **_DEBUG_OBSOLETO}


@router.get("/checks/tasa-fija")
def check_tasa_fija():
    """Estado de instrumentos tasa_fija en AuM."""
    curvas_tf = curvas_sql.por_curva("tasa_fija")   # mercado.curvas (SQL)
    if not curvas_tf:
        return {"snapshot": None, "ok": 0, "sin_posicion": 0, "sin_assets": 0, "instrumentos": []}

    t2u: dict[str, list] = {}
    for a in assets_rows(["TICKER"]):   # SQL portafolio.assets
        t2u.setdefault(a["TICKER"], []).append(a["unidad"])

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        fm = cur.fetchone()[0]
        con_pos: set = set()
        if fm:
            cur.execute("SELECT unidad FROM portafolio.tenencia "
                        "WHERE fecha = %s AND aum = 'si' AND COALESCE(valuacion, 0) <> 0", (fm,))
            con_pos = {r[0] for r in cur.fetchall()}

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


@router.get("/checks/debug-forward")
def debug_forward(
    tc_a: str = Query(..., description="ticker_corto instrumento A"),
    tc_b: str = Query(..., description="ticker_corto instrumento B"),
):
    """OBSOLETO post-decomiso Mongo — leía TEA/duration por-trade de Trading.TimeSales."""
    return {"tc_a": tc_a, "tc_b": tc_b, "forward": None, "pasos": [], **_DEBUG_OBSOLETO}


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


@router.get("/checks/futuros-dlr")
def check_futuros_dlr():
    """Debug de la curva de futuros DLR — spot, fuente y TNA por outright.

    Lee Trading.FuturosDLRSnapshot tal como lo escribe el motor (no recalcula).
    Útil para confirmar:
      - Qué spot está usando el motor y de qué fuente cayó (oficial / a3500 / mep).
      - Hace cuánto se reescribió el snapshot (stale_min). Fuera de horario de
        mercado los docs quedan viejos — esperable.
      - Dispersión TNA bid vs last vs offer en outrights cortos (ABR/MAY) donde
        un last desactualizado distorsiona la TNA reportada en la watchlist.
    """
    from datetime import UTC, datetime

    from api.services.debug_derivados import _futuros_dlr_docs
    docs = _futuros_dlr_docs()

    if not docs:
        return {"spot": None, "outrights": [], "total": 0}

    primero = docs[0]
    ts = primero.get("updated_at")
    stale_min: float | None = None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        stale_min = round((datetime.now(UTC) - ts).total_seconds() / 60.0, 1)

    spot = {
        "valor":      primero.get("spot_referencia"),
        "fuente":     primero.get("fuente_spot"),
        "stale_min":  stale_min,
    }

    outrights = [
        {
            "ticker":     d.get("ticker"),
            "vto":        d.get("vencimiento"),
            "dias":       d.get("dias_a_vto"),
            "bid":        d.get("bid_price"),
            "last":       d.get("last_price"),
            "offer":      d.get("offer_price"),
            "tna_bid":    d.get("tasa_implicita_tna_bid"),
            "tna_last":   d.get("tasa_implicita_tna"),
            "tna_offer":  d.get("tasa_implicita_tna_offer"),
            "updated_at": d.get("updated_at"),
        }
        for d in docs
    ]

    return {"spot": spot, "outrights": outrights, "total": len(docs)}


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
