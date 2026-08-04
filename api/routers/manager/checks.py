"""GET /api/manager/checks/* — validaciones de consistencia sobre Mongo."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.services.assets_sql import assets_rows
from core import curvas_sql
from core.postgres import get_pool

router = APIRouter()


def _futuros_dlr_docs() -> list[dict]:
    """Docs de mercado.futuros_dlr_snapshot (jsonb `data`) ordenados por vencimiento.
    SQL-native (decomiso Mongo: Trading.FuturosDLRSnapshot dropeada)."""
    from psycopg.rows import dict_row
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data FROM mercado.futuros_dlr_snapshot ORDER BY vencimiento")
        return [r["data"] for r in cur.fetchall() if r["data"]]


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
    """Reproduce paso a paso el cálculo del branch soberano de engines/curvas.

    Devuelve el instrumento de Trading.Curvas, el precio convertido a USD
    (aplicando MEP si corresponde), los flujos futuros al settlement con
    su monto calculado, el cashflow que entra al XIRR, y el TEA/duration/
    paridad resultante. Útil para diagnosticar cuando un bono da TEA rara.
    """
    from datetime import UTC, date, datetime

    from engines.curvas import (
        cargar_dias_habiles,
        cargar_mep_actual,
        fecha_flujo,
        macaulay_duration,
        monto_flujo_soberano,
        precio_soberano_a_usd,
        siguiente_dia_habil,
        xirr,
    )

    inst = curvas_sql.find_one(ticker_corto)   # mercado.curvas (SQL)
    if not inst:
        raise HTTPException(404, f"No existe ticker_corto={ticker_corto!r} en mercado.curvas")

    curva = inst.get("curva")
    if curva != "soberanos":
        raise HTTPException(
            400,
            f"Este check solo aplica a curva='soberanos' — el ticker tiene curva={curva!r}",
        )

    # ── Instrumento + último precio (mercado.timesales, SQL-native) ─────
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT price, ts FROM mercado.timesales WHERE ticker = %s "
            "ORDER BY ts DESC LIMIT 1", (inst["ticker"],))
        row = cur.fetchone()
    precio = float(row[0]) if row and row[0] is not None else None
    ts_trade = row[1] if row else None

    mep = cargar_mep_actual()
    precio_usd = precio_soberano_a_usd(precio, inst.get("ticker") or "", mep) if precio else None

    # ── Settlement ─────────────────────────────────────────────────────
    dias_habiles = cargar_dias_habiles()
    hoy = datetime.now(UTC).date()
    settlement_str = siguiente_dia_habil(dias_habiles, hoy)
    fecha_settlement = date.fromisoformat(settlement_str) if settlement_str else hoy

    # ── Flujos futuros ─────────────────────────────────────────────────
    flujos_raw = inst.get("flujos") or []
    valor_nominal = float(inst.get("valor_nominal", 100))
    flujos_futuros: list[dict] = []
    for f in flujos_raw:
        fd = fecha_flujo(f)
        if not fd or fd <= fecha_settlement:
            continue
        monto = monto_flujo_soberano(f, valor_nominal)
        if monto <= 0:
            continue
        flujos_futuros.append({
            "fecha":              fd.isoformat(),
            "amortizacion_pct":   f.get("amortizacion_pct", 0),
            "cupon_sobre_residual": f.get("cupon_sobre_residual", 0),
            "residual_previo_pct": f.get("residual_previo_pct", 0),
            "monto_usd":          round(monto, 4),
        })

    total_flujos = round(sum(f["monto_usd"] for f in flujos_futuros), 4)

    # ── XIRR / duration / paridad ──────────────────────────────────────
    tea = None
    duration = None
    paridad = None
    cashflow: list[dict] = []
    if precio_usd and flujos_futuros:
        cashflow.append({"fecha": fecha_settlement.isoformat(), "monto": -round(precio_usd, 4)})
        for f in flujos_futuros:
            cashflow.append({"fecha": f["fecha"], "monto": f["monto_usd"]})

        fechas_dt = [datetime.combine(fecha_settlement, datetime.min.time())] + \
                    [datetime.combine(date.fromisoformat(f["fecha"]), datetime.min.time()) for f in flujos_futuros]
        cf = [-precio_usd] + [f["monto_usd"] for f in flujos_futuros]

        tea_val = xirr(fechas_dt, cf)
        if tea_val is not None:
            tea = round(tea_val, 6)
            fechas_flujos_dt = [datetime.combine(date.fromisoformat(f["fecha"]), datetime.min.time()) for f in flujos_futuros]
            montos_flujos = [f["monto_usd"] for f in flujos_futuros]
            fecha_base_dt = datetime.combine(fecha_settlement, datetime.min.time())
            duration = macaulay_duration(fechas_flujos_dt, montos_flujos, tea_val, fecha_base_dt)

    if precio_usd and flujos_futuros:
        residual_vivo = flujos_futuros[0]["residual_previo_pct"]
        if residual_vivo:
            paridad = round(precio_usd / float(residual_vivo) * 100, 4)

    return {
        "instrumento": {
            "ticker":             inst.get("ticker"),
            "ticker_corto":       inst.get("ticker_corto"),
            "tipo":               inst.get("tipo"),
            "curva":              curva,
            "fecha_emision":      inst.get("fecha_emision"),
            "fecha_vencimiento":  inst.get("fecha_vencimiento"),
            "valor_nominal":      valor_nominal,
            "flujos_total":       len(flujos_raw),
        },
        "precio": {
            "ultimo_trade_ts": ts_trade.isoformat() if hasattr(ts_trade, "isoformat") else None,
            "precio_rofex":   precio,
            "mep":            mep,
            "precio_usd":     round(precio_usd, 4) if precio_usd else None,
        },
        "settlement": fecha_settlement.isoformat(),
        "flujos_futuros": flujos_futuros,
        "total_flujos_usd": total_flujos,
        "cashflow": cashflow,
        "resultado": {
            "tea_pct":  round(tea * 100, 4) if tea is not None else None,
            "duration": duration,
            "paridad":  paridad,
        },
    }


@router.get("/checks/breakevens-debug")
def check_breakevens_debug():
    """Desglose paso a paso del BE para cada par Lecap/Boncap ↔ CER del
    motor live. Devuelve tanto Buscar Objetivo como Fisher clásico para
    que la mesa compare los dos.

    Buscar Objetivo (preferido, usa precio y flujo directos):
      retorno_lecap = flujo_vto_lecap / precio_lecap − 1
      factor        = (1+retorno_lecap) × (precio_cer × cer_emision)
                                       / (vn_cer × cer_actual)
      BE            = factor^(1/meses_pendientes) − 1

    Fisher clásico (fallback, usa TEM y paridad):
      R   = (1 + TEM)^(días/30) − 1
      π   = (1 + R) × (paridad/100) − 1
      BE  = (1 + π)^(30/días) − 1

    meses_pendientes = días entre cer_max publicado y la fecha de liquidación
    del CER del bono (vto − 10 hábiles).
    """
    from datetime import date

    from engines.breakevens import (
        cargar_dias_habiles,
        cargar_pares,
        obtener_paridades,
        obtener_precios,
        obtener_tems,
        obtener_valor_cer,
        ultimo_cer_publicado,
    )
    from engines.curvas import fecha_cer_liquidacion

    # SQL-native (decomiso Mongo): los helpers de engines.breakevens leen SQL
    # (market_snapshot/series_macro/dias_habiles) e ignoran el 1er arg.
    pares = cargar_pares()
    if not pares:
        return {"pares": [], "fecha_cer_max": None, "cer_actual": None}

    lecap_tickers = [p["lecap_ticker"] for p in pares]
    cer_tickers   = [p["cer_ticker"]   for p in pares]
    tems          = obtener_tems(lecap_tickers)
    paridades     = obtener_paridades(cer_tickers)
    precios       = obtener_precios(lecap_tickers + cer_tickers)
    dias_habiles  = cargar_dias_habiles()
    fecha_cer_max = ultimo_cer_publicado()
    cer_actual    = obtener_valor_cer(fecha_cer_max) if fecha_cer_max else None

    hoy = date.today()
    filas = []
    for par in pares:
        try:
            fecha_vto = date.fromisoformat(par["fecha_vencimiento"])
        except Exception:
            continue
        dias = (fecha_vto - hoy).days
        if dias < 30:
            continue

        tem = tems.get(par["lecap_ticker"])
        paridad = paridades.get(par["cer_ticker"])
        precio_lecap = precios.get(par["lecap_ticker"])
        precio_cer   = precios.get(par["cer_ticker"])
        flujo_vto_lecap = par.get("flujo_vto_lecap")
        vn_cer       = par.get("vn_cer") or 100
        cer_emision  = par.get("cer_emision")

        fecha_liq_cer_str = fecha_cer_liquidacion(
            dias_habiles, par["fecha_vencimiento"], n=10,
        )
        fecha_liq_cer = (
            date.fromisoformat(fecha_liq_cer_str) if fecha_liq_cer_str else None
        )
        meses_pendientes = None
        if fecha_liq_cer and fecha_cer_max:
            try:
                fecha_cer_max_d = date.fromisoformat(fecha_cer_max)
                delta_dias = (fecha_liq_cer - fecha_cer_max_d).days
                if delta_dias > 0:
                    meses_pendientes = delta_dias / 30.0
            except Exception:
                pass

        # Buscar Objetivo
        be_bo = None
        factor_bo = None
        retorno_lecap_directo = None
        if (
            precio_lecap and precio_cer and flujo_vto_lecap
            and cer_emision and cer_actual and meses_pendientes
        ):
            try:
                retorno_lecap_directo = float(flujo_vto_lecap) / float(precio_lecap) - 1
                factor_bo = (
                    (1 + retorno_lecap_directo)
                    * float(precio_cer) * float(cer_emision)
                    / (float(vn_cer) * float(cer_actual))
                )
                if factor_bo > 0:
                    be_bo = factor_bo ** (1 / meses_pendientes) - 1
            except Exception:
                pass

        # Fisher clásico
        be_fisher = None
        retorno_fisher = None
        inflacion_fisher = None
        if tem is not None and paridad is not None:
            try:
                retorno_fisher = (1 + float(tem)) ** (dias / 30) - 1
                inflacion_fisher = (1 + retorno_fisher) * (float(paridad) / 100) - 1
                be_fisher = (1 + inflacion_fisher) ** (30 / dias) - 1
            except Exception:
                pass

        filas.append({
            "lecap":             par["lecap_corto"],
            "cer":               par["cer_corto"],
            "fecha_vto":         par["fecha_vencimiento"],
            "dias":              dias,
            "fecha_cer_liq":     fecha_liq_cer.isoformat() if fecha_liq_cer else None,
            "meses_pendientes":  round(meses_pendientes, 4) if meses_pendientes else None,
            # Buscar Objetivo
            "precio_lecap":      round(float(precio_lecap), 4) if precio_lecap else None,
            "flujo_vto_lecap":   flujo_vto_lecap,
            "precio_cer":        round(float(precio_cer), 4) if precio_cer else None,
            "vn_cer":            vn_cer,
            "cer_emision":       cer_emision,
            "retorno_lecap":     round(retorno_lecap_directo, 6) if retorno_lecap_directo is not None else None,
            "factor_bo":         round(factor_bo, 6) if factor_bo else None,
            "be_buscar_obj":     round(be_bo, 6) if be_bo else None,
            # Fisher (comparación)
            "tem_lecap":         round(float(tem), 6) if tem is not None else None,
            "paridad_cer":       round(float(paridad), 4) if paridad is not None else None,
            "retorno_fisher":    round(retorno_fisher, 6) if retorno_fisher is not None else None,
            "inflacion_fisher":  round(inflacion_fisher, 6) if inflacion_fisher is not None else None,
            "be_fisher":         round(be_fisher, 6) if be_fisher is not None else None,
        })

    return {
        "fecha_cer_max": fecha_cer_max,
        "cer_actual":    cer_actual,
        "pares":         filas,
    }


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
    """Validación paso-a-paso del cálculo de TNA implícita por outright DLR.

    El motor persiste UN solo número en `tasa_implicita_tna` calculado como
    TEA compuesta: `((dlr/spot)^(365/dias) - 1) * 100`. Pero la mesa puede
    estar mirando TNA lineal en el terminal Rofex. Este endpoint muestra
    AMBAS convenciones por outright para que se pueda confirmar contra
    cualquier referencia externa cuál matchea.

    Cálculos por outright (sobre last):
      directo = (last / spot - 1) × 100              → % absoluto al vto
      tna_lineal = directo × (365 / dias)            → anualizado lineal
      tea_compuesta = ((last/spot)^(365/dias) - 1)×100  → anualizado compuesto

    Diferencia esperada:
      30 días  → tna_lineal ≈ tea_compuesta - 0.5
      90 días  → tna_lineal ≈ tea_compuesta - 1.5
      180 días → tna_lineal ≈ tea_compuesta - 3
      365 días → tna_lineal == tea_compuesta (idénticas)
    """
    docs = _futuros_dlr_docs()

    if not docs:
        return {
            "spot":   {"valor": None, "fuente": None},
            "filas":  [],
            "total":  0,
            "nota":   (
                "Sin docs en Trading.FuturosDLRSnapshot. El motor de futuros DLR "
                "probablemente está caído o aún no escribió. Reiniciá "
                "motor_futuros_dlr.service y volvé a ejecutar."
            ),
        }

    primero = docs[0]
    spot_val = primero.get("spot_referencia")
    spot_fuente = primero.get("fuente_spot")

    def _directo(px, spot, dias):
        if not px or not spot or spot <= 0 or dias <= 0:
            return None
        return round((px / spot - 1) * 100, 4)

    def _tna_lineal(px, spot, dias):
        d = _directo(px, spot, dias)
        if d is None:
            return None
        return round(d * (365 / dias), 4)

    def _tea_compuesta(px, spot, dias):
        if not px or not spot or spot <= 0 or dias <= 0:
            return None
        return round(((px / spot) ** (365 / dias) - 1) * 100, 4)

    filas = []
    for d in docs:
        last = d.get("last_price")
        bid = d.get("bid_price")
        offer = d.get("offer_price")
        dias = d.get("dias_a_vto") or 1
        mid_book = (bid + offer) / 2 if (bid and offer) else None

        filas.append({
            "ticker":             d.get("ticker"),
            "vto":                d.get("vencimiento"),
            "dias":               dias,
            "bid":                bid,
            "last":               last,
            "offer":              offer,
            "mid_book":           round(mid_book, 4) if mid_book else None,
            # Cálculos sobre last
            "directo_last":       _directo(last, spot_val, dias),
            "tna_lineal_last":    _tna_lineal(last, spot_val, dias),
            "tea_compuesta_last": _tea_compuesta(last, spot_val, dias),
            # Cálculos sobre mid del book (si hay puntas)
            "tna_lineal_mid":     _tna_lineal(mid_book, spot_val, dias),
            "tea_compuesta_mid":  _tea_compuesta(mid_book, spot_val, dias),
            # Lo que ESTÁ persistido (TEA hoy, a pesar del nombre)
            "tna_persistida":     d.get("tasa_implicita_tna"),
        })

    return {
        "spot":   {"valor": spot_val, "fuente": spot_fuente},
        "filas":  filas,
        "total":  len(filas),
        "nota":   (
            "El motor persiste TNA LINEAL en 'tasa_implicita_tna' "
            "(convención terminal Rofex). La columna PERSISTIDA debería "
            "matchear TNA LIN (last). Si ves TEA COMP, es porque todavía "
            "no reiniciaste motor_futuros_dlr.service después del último "
            "deploy."
        ),
    }


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
