"""Capa de servicio — analítica Tier 2 sobre data existente.

Tres herramientas que operan encima de `renta_fija.listar_curva` y
`Trading.TimeSales` para responder preguntas analíticas:

- `snapshot_curva_historico(curva, fecha)` — curva entera como cerró en un día pasado.
- `calcular_pendiente_curva(curva, metrica, fecha_comparacion)` — slope en bps ± comparación.
- `liquidez_secundario(ticker, dias)` — volumen del día vs promedio N ruedas.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.cache import cached
from api.db import get_db_trading
from api.services.renta_fija import _CURVAS_VALIDAS, listar_curva, resolver_ticker_exacto


@cached(ttl=300)
def snapshot_curva_historico(curva: str, fecha: str) -> list[dict]:
    """Curva entera tal como cerró un día pasado.

    Para cada bono de la curva, busca el último trade de ese día en
    Trading.TimeSales y devuelve el mismo shape que listar_curva() (ticker,
    ticker_corto, precio, TEA, TEM, paridad, duration, convexity).

    Si un bono no operó ese día, no aparece en el resultado (vs invents).
    """
    if curva not in _CURVAS_VALIDAS:
        return []

    db = get_db_trading()

    # cupon_anual + cer_emision: necesarios para que la descomposición de
    # retorno CER pueda distinguir Lecers (zero coupon) de Boncers cupón
    # y des-indexar precios sucios a paridad real. Para el resto de las
    # curvas son no-ops (los docs no traen esos campos).
    curva_docs = list(db["Curvas"].find(
        {"curva": curva},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1,
         "fecha_vencimiento": 1, "fecha_emision": 1,
         "cupon_anual": 1, "cer_emision": 1},
    ))
    if not curva_docs:
        return []

    tickers = [d["ticker"] for d in curva_docs if d.get("ticker")]
    meta_by_ticker = {d["ticker"]: d for d in curva_docs if d.get("ticker")}

    try:
        fecha_dt = datetime.fromisoformat(fecha[:10]).replace(tzinfo=UTC)
    except ValueError:
        return []
    fin_dt = fecha_dt + timedelta(days=1)

    enrich: dict[str, dict] = {}
    for r in db["TimeSales"].aggregate([
        {"$match": {
            "ticker": {"$in": tickers},
            "timestamp": {"$gte": fecha_dt, "$lt": fin_dt},
            "price": {"$gt": 0},
        }},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id":       "$ticker",
            "price":     {"$first": "$price"},
            "TEA":       {"$first": "$TEA"},
            "TEM":       {"$first": "$TEM"},
            "paridad":   {"$first": "$paridad"},
            "duration":     {"$first": "$duration"},
            "mod_duration": {"$first": "$mod_duration"},
            "convexity":    {"$first": "$convexity"},
            "ts":           {"$first": "$timestamp"},
        }},
    ]):
        enrich[r["_id"]] = r

    out: list[dict] = []
    ahora = fecha_dt
    for ticker, m in meta_by_ticker.items():
        if ticker not in enrich:
            continue
        en = enrich[ticker]
        vto_raw = m.get("fecha_vencimiento")
        meses = None
        try:
            if isinstance(vto_raw, datetime):
                vto = vto_raw if vto_raw.tzinfo else vto_raw.replace(tzinfo=UTC)
            else:
                vto = datetime.fromisoformat(str(vto_raw)[:10]).replace(tzinfo=UTC)
            meses = round((vto - ahora).days / 30.44, 1)
        except Exception:
            pass
        ts_last = en.get("ts")
        entry = {
            "ticker":             ticker,
            "ticker_corto":       m.get("ticker_corto"),
            "tipo":               m.get("tipo"),
            "fecha_vencimiento":  str(m.get("fecha_vencimiento"))[:10] if m.get("fecha_vencimiento") else None,
            "meses_al_vto":       meses,
            "ultimo_precio":      en.get("price"),
            "tea":                en.get("TEA"),
            "tem":                en.get("TEM"),
            "paridad":             en.get("paridad"),
            "duration":           en.get("duration"),
            "mod_duration":       en.get("mod_duration"),
            "convexity":          en.get("convexity"),
            "ts_ultimo_trade":    ts_last.isoformat() if isinstance(ts_last, datetime) else ts_last,
        }
        # Metadatos extra para curva CER (descomposición de retorno).
        if curva == "cer":
            cupon = m.get("cupon_anual")
            entry["is_zero_coupon"] = (cupon is None) or (float(cupon) == 0.0)
            cer_em = m.get("cer_emision")
            if cer_em:
                entry["cer_emision"] = float(cer_em)
        out.append(entry)
    out.sort(key=lambda x: x.get("fecha_vencimiento") or "9999")
    return out


_METRICAS_PENDIENTE = ("tea", "tem", "duration")


@cached(ttl=60)
def calcular_pendiente_curva(
    curva: str,
    metrica: str = "tea",
    fecha_comparacion: str | None = None,
    dias_min_corto: int = 30,
) -> dict:
    """Pendiente de una curva (valor largo − valor corto).

    "Corto" = menor duration entre los bonos con vencimiento ≥ `dias_min_corto`
    días. "Largo" = mayor duration. El resultado se expresa en basis points (bps).
    Opcionalmente compara con la curva del día `fecha_comparacion` y devuelve el
    delta de pendiente (útil para detectar empinamiento/aplanamiento).

    `dias_min_corto` (default 30) excluye bonos a punto de vencer del anchor
    "corto" — sus TEAs son ruidosas (microestructura de fin de plazo) e
    inflan artificialmente el spread. Bajalo a 0 si querés incluir todo.

    metrica ∈ {tea, tem, duration}. Default tea.
    """
    if curva not in _CURVAS_VALIDAS:
        return {"error": f"curva inválida: {curva}"}
    metrica = metrica.lower()
    if metrica not in _METRICAS_PENDIENTE:
        return {"error": f"metrica inválida: {metrica}"}

    ahora = listar_curva(curva=curva, ordenar_por="duration")
    meses_min = dias_min_corto / 30.0
    validos = [
        b for b in ahora
        if b.get(metrica) is not None
        and b.get("duration")
        and (b.get("meses_al_vto") or 0) >= meses_min
    ]
    if len(validos) < 2:
        return {
            "error": f"insuficientes instrumentos con metrica + duration "
                     f"+ vto ≥ {dias_min_corto} días",
        }

    corto_now = validos[0]
    largo_now = validos[-1]
    valor_corto_now = float(corto_now[metrica])
    valor_largo_now = float(largo_now[metrica])
    pendiente_actual_bps = round((valor_largo_now - valor_corto_now) * 10000, 0)

    out: dict = {
        "curva":                   curva,
        "metrica":                 metrica,
        "pendiente_actual_bps":    pendiente_actual_bps,
        "corto": {
            "ticker":   corto_now.get("ticker_corto") or corto_now.get("ticker"),
            "duration": corto_now.get("duration"),
            metrica:    valor_corto_now,
        },
        "largo": {
            "ticker":   largo_now.get("ticker_corto") or largo_now.get("ticker"),
            "duration": largo_now.get("duration"),
            metrica:    valor_largo_now,
        },
    }

    if fecha_comparacion:
        hist = snapshot_curva_historico(curva=curva, fecha=fecha_comparacion)
        hist_validos = [b for b in hist if b.get(metrica) is not None and b.get("duration")]
        if len(hist_validos) >= 2:
            hist_validos.sort(key=lambda b: b["duration"])
            corto_hist = hist_validos[0]
            largo_hist = hist_validos[-1]
            pend_hist_bps = round(
                (float(largo_hist[metrica]) - float(corto_hist[metrica])) * 10000, 0
            )
            delta = round(pendiente_actual_bps - pend_hist_bps, 0)
            if delta > 10:
                interpretacion = "empinamiento"
            elif delta < -10:
                interpretacion = "aplanamiento"
            else:
                interpretacion = "sin cambio material"
            out.update({
                "fecha_comparacion":          fecha_comparacion,
                "pendiente_comparacion_bps":  pend_hist_bps,
                "delta_bps":                  delta,
                "interpretacion":             interpretacion,
            })
        else:
            out["fecha_comparacion"] = fecha_comparacion
            out["error_comparacion"] = "insuficientes instrumentos en fecha pedida"

    return out


def _clasificar_liquidez(ratio: float | None) -> str:
    if ratio is None:
        return "sin_datos"
    if ratio < 0.3:
        return "baja"
    if ratio <= 1.5:
        return "media"
    if ratio <= 3.0:
        return "alta"
    return "anomalamente_alta"


@cached(ttl=60)
def liquidez_secundario(ticker: str, dias: int = 20) -> dict:
    """Volumen operado del día actual vs promedio histórico (Trading.TimeSales).

    Usa TimeSales para consistencia (en vez de MarketSnapshot) porque el
    histórico agrupado por día siempre sale de ahí. dias = ventana para el
    promedio. Clasificación: baja | media | alta | anomalamente_alta.
    """
    ticker_exacto = resolver_ticker_exacto(ticker)
    if not ticker_exacto:
        return {"error": f"no se pudo resolver ticker '{ticker}'"}

    db = get_db_trading()
    hoy = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    desde = hoy - timedelta(days=dias)

    agg = list(db["TimeSales"].aggregate([
        {"$match": {
            "ticker": ticker_exacto,
            "timestamp": {"$gte": desde},
            "money": {"$gt": 0},
        }},
        {"$group": {
            "_id":   {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            "money": {"$sum": "$money"},
        }},
        {"$sort": {"_id": 1}},
    ]))

    if not agg:
        return {
            "ticker":              ticker_exacto,
            "volumen_dia_actual":  0,
            "volumen_promedio_dia": None,
            "ratio_vs_promedio":   None,
            "dias_analizados":     0,
            "clasificacion":       "sin_datos",
        }

    hoy_str = hoy.strftime("%Y-%m-%d")
    hoy_row = next((r for r in agg if r["_id"] == hoy_str), None)
    vol_hoy = float(hoy_row["money"]) if hoy_row else 0.0

    hist = [float(r["money"]) for r in agg if r["_id"] != hoy_str]
    promedio = round(sum(hist) / len(hist), 2) if hist else None

    ratio = None
    if promedio and promedio > 0:
        ratio = round(vol_hoy / promedio, 3)

    return {
        "ticker":               ticker_exacto,
        "volumen_dia_actual":   round(vol_hoy, 2),
        "volumen_promedio_dia": promedio,
        "ratio_vs_promedio":    ratio,
        "dias_analizados":      len(hist),
        "clasificacion":        _clasificar_liquidez(ratio),
    }
