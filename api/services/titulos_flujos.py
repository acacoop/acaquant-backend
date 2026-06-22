"""api/services/titulos_flujos.py — flujos normalizados por instrumento.

Arma en runtime, desde `Trading.Curvas` (UNA sola base: la `curva` decide la vista,
las ONs son curva on_<sector>), un doc por instrumento con los flujos normalizados
(join key con assets: `ticker`). BondsMaster fue RETIRADO (consolidado en Curvas,
2026-06-22). Es un set chico (~169 docs) → barato por request. Los callers ya cachean
su resultado (endpoints @cached), por eso esta función NO cachea: devuelve dicts
frescos en cada llamada para que el caller pueda mutarlos sin ensuciar ningún cache.
"""
from __future__ import annotations

from datetime import datetime

from api.db import get_db_trading
from core.postgres import get_pool


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


def flujos_instrumentos() -> list[dict]:
    """Flujos de los instrumentos desde Trading.Curvas. TODO vive en Curvas (la `curva`
    decide la vista); BondsMaster se consolidó en Curvas (`on_*`) → ya NO se mergea
    (verificado no-op: los 169 BM están en Curvas, dedup por ticker_corto). Dicts frescos."""
    db = get_db_trading()
    return [_build_from_curvas(d) for d in db["Curvas"].find({}, {"_id": 0})]


_ASSETS_COLS = ("unidad", "calificacion", "cartera", "clase_activo",
                "emisor", "ticker", "instrumento", "vencimiento")


def assets_normalizados() -> list[dict]:
    """Catálogo de títulos desde SQL `portafolio.assets` (la fuente de verdad que
    edita Manager → Assets). Mismo shape minúscula de siempre — antes leía Mongo
    Valuaciones.Assets; ahora SQL (migración assets→SQL). Set chico (~1660); los
    callers cachean. `vencimiento` ('YYYY-MM-DD') → datetime solo-fecha o None.
    Los vacíos vienen NULL de SQL → se normalizan a '' para no romper el contrato."""
    out = []
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(_ASSETS_COLS)} FROM portafolio.assets")
        for row in cur.fetchall():
            d = dict(zip(_ASSETS_COLS, row, strict=True))
            out.append({
                "unidad": d["unidad"] or "",
                "calificacion": d["calificacion"] or "",
                "cartera": d["cartera"] or "",
                "clase_activo": d["clase_activo"] or "",
                "emisor": d["emisor"] or "",
                "ticker": d["ticker"] or "",
                "instrumento": d["instrumento"] or "",
                "vencimiento": _fecha_to_datetime(d["vencimiento"]),
            })
    return out
