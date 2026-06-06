"""api/services/titulos_flujos.py — flujos normalizados por instrumento.

Reemplaza el espejo materializado `TitulosAPI.ValuacionesAPI`: arma en runtime
el merge `Trading.Curvas` + `Trading.BondsMaster` con los flujos normalizados,
un doc por instrumento (join key con assets: `ticker`). Es un set chico (~121
docs) → barato de computar por request. Los callers ya cachean su resultado
(endpoints @cached), por eso esta función NO cachea: devuelve dicts frescos en
cada llamada para que el caller pueda mutarlos sin ensuciar ningún cache.

Lógica idéntica a la que tenía `scripts/api_migrate.migrate_flujos_titulos`.
"""
from __future__ import annotations

from datetime import datetime

from api.db import get_db_trading, get_db_valuaciones


def _fecha_to_datetime(raw) -> datetime | None:
    """String 'YYYY-MM-DD' o datetime → datetime solo fecha. None si falla."""
    if isinstance(raw, datetime):
        return datetime(raw.year, raw.month, raw.day)
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.strptime(raw.strip()[:10], "%Y-%m-%d")
        return datetime(dt.year, dt.month, dt.day)
    except (ValueError, AttributeError):
        return None


def _calcular_residual_actual(flujos: list[dict]) -> float | None:
    """Residual del último flujo cuya fecha ya pasó. 100 si ninguno pasó; None si no hay."""
    if not flujos:
        return None
    hoy = datetime.now()
    ultimo_residual = 100.0
    for f in flujos:
        fecha = f.get("fecha")
        if fecha and fecha <= hoy:
            residual = f.get("residual")
            if residual is not None:
                ultimo_residual = residual
    return ultimo_residual


def _build_from_curvas(doc: dict) -> dict:
    flujos = [
        {
            "fecha": _fecha_to_datetime(f.get("fecha")),
            "amortizacion": f.get("amortizacion_pct", f.get("amortizacion")),
            "interes": f.get("cupon_sobre_residual", f.get("interes")),
            "residual": f.get("residual_previo_pct", f.get("valor_residual")),
        }
        for f in (doc.get("flujos", []) or [])
    ]
    return {
        "ticker": doc.get("ticker_corto", ""),
        "instrumento": doc.get("ticker", ""),
        "curva": doc.get("curva", ""),
        "moneda_flujo": "",
        "fecha_emision": _fecha_to_datetime(doc.get("fecha_emision")),
        "fecha_vencimiento": _fecha_to_datetime(doc.get("fecha_vencimiento")),
        "valor_nominal": doc.get("valor_nominal"),
        "cupon_anual": doc.get("cupon_anual"),
        "cer_emision": doc.get("cer_emision"),
        "tasa_cupon": None,
        "flujo_vencimiento": doc.get("flujo_vencimiento"),
        "valor_residual_actual_pct": doc.get("valor_residual_actual_pct"),
        "flujos": flujos,
    }


def _build_from_bondmaster(doc: dict) -> dict:
    flujos = [
        {
            "fecha": _fecha_to_datetime(f.get("fecha")),
            "amortizacion": f.get("amortizacion"),
            "interes": f.get("interes"),
            "residual": f.get("valor_residual"),
        }
        for f in (doc.get("flujos", []) or [])
    ]
    tickers = doc.get("tickers", {}) or {}
    instrumento = tickers.get("ARS") or tickers.get("USD") or ""
    return {
        "ticker": doc.get("asset", ""),
        "instrumento": instrumento,
        "curva": "",
        "moneda_flujo": doc.get("moneda_flujo", ""),
        "fecha_emision": None,
        "fecha_vencimiento": _fecha_to_datetime(doc.get("vencimiento")),
        "valor_nominal": 100,
        "cupon_anual": None,
        "cer_emision": None,
        "tasa_cupon": doc.get("tasa_cupon"),
        "flujo_vencimiento": None,
        "valor_residual_actual_pct": _calcular_residual_actual(flujos),
        "flujos": flujos,
    }


def flujos_instrumentos() -> list[dict]:
    """Merge Curvas + BondsMaster (dedup por ticker, prevalece Curvas). Dicts frescos."""
    db = get_db_trading()
    out = [_build_from_curvas(d) for d in db["Curvas"].find({}, {"_id": 0})]
    tickers_curvas = {d["ticker"] for d in out}
    for d in db["BondsMaster"].find({}, {"_id": 0}):
        doc = _build_from_bondmaster(d)
        if doc["ticker"] not in tickers_curvas:
            out.append(doc)
    return out


# unidad NO se renombra (ya está en minúscula en la fuente); el resto es UPPER→lower.
_ASSETS_MAP = {
    "calificacion": "CALIFICACION", "cartera": "CARTERA", "clase_activo": "CLASE_ACTIVO",
    "emisor": "EMISOR", "ticker": "TICKER", "instrumento": "INSTRUMENTO",
}


def assets_normalizados() -> list[dict]:
    """Valuaciones.Assets (UPPERCASE, fuente que edita Manager → Assets) → docs en
    minúscula, mismo shape que servía el ex-espejo TitulosAPI.AssetsAPI. Set chico
    (~1630 docs); los callers cachean. VENCIMIENTO ('YYYY-MM-DD HH:MM:SS' o
    'NO APLICA') → datetime solo-fecha o None."""
    out = []
    for d in get_db_valuaciones()["Assets"].find({}, {"_id": 0}):
        row = {"unidad": d.get("unidad", "")}
        for lo, up in _ASSETS_MAP.items():
            row[lo] = d.get(up, "")
        row["vencimiento"] = _fecha_to_datetime(d.get("VENCIMIENTO"))
        out.append(row)
    return out
