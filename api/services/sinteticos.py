"""Sintéticos — combinaciones LECAP/DLK + futuro DLR.

Dos tablas, ambas con lógica automática (matcheo por año-mes de vencimiento):

1. **LONG ROFEX + LONG LECAP** (sintético en dólares):
   - Pagás hoy `Px_TF` (ARS) por la LECAP; al vto cobrás `Cobro` (ARS).
   - Compraste futuro DLR a `Px_Futuro` (locked) → vendés esos pesos al
     futuro y obtenés USD.
   - Comparás USD invertidos vs. USD obtenidos:
       T+0 = Px_TF / SPOT
       T+n = Cobro / Px_Futuro
       TE  = T+n / T+0 − 1
       TNA = TE × 365 / días     (anualización lineal)

2. **SHORT ROFEX + LONG DLK** (sintético en pesos / lock de tasa):
   - Pagás `Px_DLK` (ARS) por el bono dólar linked; al vto cobrás
     `face × DLR_oficial` (ARS).
   - Vendés futuro DLR a `Px_Futuro` → fijás el DLR de salida.
   - El cobro queda fijo en ARS y la TE comparado con el precio pagado:
       TE  = Px_Futuro / Px_DLK − 1
       TNA = TE × 365 / días

El match LECAP/DLK ↔ futuro DLR se hace por (año, mes) de vencimiento — el
sistema agarra cualquier ticker nuevo que aparezca en `Trading.Curvas` o en
los snapshots de futuros sin que haya que tocar código.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from api.cache import cached
from core.dolar_oficial import mid_oficial_live
from core.mongo import get_mongo_client_read


def _parse_yyyymmdd(s: str | None) -> date | None:
    """`'20260531'` → `date(2026,5,31)`. None si no parsea."""
    if not s or len(s) != 8:
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (TypeError, ValueError):
        return None


def _to_date(d: Any) -> date | None:
    """Acepta datetime/date de Mongo o str ISO; devuelve `date` o None."""
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d[:10]).date()
        except ValueError:
            return None
    return None


def _div(a: float | None, b: float | None) -> float | None:
    """`a/b` defensivo — None si falta alguno o b≈0."""
    if a is None or b is None or b == 0:
        return None
    return a / b


def _annualize_lin(te: float | None, dias: int) -> float | None:
    """TE × 365 / días. None si dias inválido."""
    if te is None or dias <= 0:
        return None
    return te * 365 / dias


@cached(ttl=5)
def get_sinteticos() -> dict[str, Any]:
    """Devuelve ambas tablas. Cache 5s (mismo ritmo que el motor).

    Output:
        {
          "spot": float | None,
          "spot_source": str,
          "spot_ts": datetime | None,
          "ts": datetime,
          "long_rofex_long_lecap": [ {ticker, futuro_ticker, px_tf, px_futuro,
              vto_fecha, futuro_vto_fecha, cobro, plazo_normal, descalce,
              t0, tn, te, tna}, ... ],
          "short_rofex_long_dlk": [ {ticker, futuro_ticker, px_dlk, px_futuro,
              dlr_ajuste, vto_dlk, vto_futuro, plazo_normal, descalce,
              te, tna}, ... ],
        }
    """
    db = get_mongo_client_read()["Trading"]
    today = date.today()
    hoy_str = today.strftime("%Y%m%d")

    # SPOT = mayorista MAE (mismo feed que motor_futuros_dlr usa para tasa
    # implícita). Si MAE está caído, mid_oficial_live cae a A3500 y al MEP.
    oficial = mid_oficial_live("oficial")
    spot = oficial.get("value")

    # Futuros DLR vigentes, indexados por (año, mes) del vencimiento.
    futuros = list(
        db["FuturosDLRSnapshot"]
        .find({"vencimiento": {"$gt": hoy_str}}, {"_id": 0})
        .sort("vencimiento", 1)
    )
    fut_by_ym: dict[tuple[int, int], dict] = {}
    for f in futuros:
        fvto = _parse_yyyymmdd(f.get("vencimiento"))
        if fvto:
            # Si hubiera más de un futuro en el mismo mes (no debería pasar),
            # nos quedamos con el de vencimiento más temprano (el primero por
            # el sort de arriba).
            fut_by_ym.setdefault((fvto.year, fvto.month), f)

    # Curvas tasa_fija con flujo_vencimiento — LECAPs / BONCAPs. CER fijados
    # quedan afuera (su shape es CER, no tienen flujo_vencimiento absoluto).
    lecaps = list(
        db["Curvas"].find(
            {
                "curva": "tasa_fija",
                "flujo_vencimiento": {"$exists": True, "$ne": None},
            },
            {
                "_id": 0,
                "ticker": 1,
                "ticker_corto": 1,
                "fecha_vencimiento": 1,
                "flujo_vencimiento": 1,
            },
        )
    )

    # Dollar linked.
    dlks = list(
        db["Curvas"].find(
            {"curva": "dolar_linked"},
            {
                "_id": 0,
                "ticker": 1,
                "ticker_corto": 1,
                "fecha_vencimiento": 1,
            },
        )
    )

    # Precios live de todos los bonos en una sola query.
    all_tickers = [
        c["ticker"] for c in (lecaps + dlks) if c.get("ticker")
    ]
    px_map: dict[str, float] = {}
    if all_tickers:
        for s in db["MarketSnapshot"].find(
            {"ticker": {"$in": all_tickers}},
            {"_id": 0, "ticker": 1, "metrics.last_price": 1},
        ):
            last = (s.get("metrics") or {}).get("last_price")
            if last:
                px_map[s["ticker"]] = last

    long_lecap = _build_long_lecap(lecaps, fut_by_ym, px_map, spot, today)
    short_dlk = _build_short_dlk(dlks, fut_by_ym, px_map, spot, today)

    return {
        "spot": spot,
        "spot_source": oficial.get("source"),
        "spot_ts": oficial.get("ts"),
        "ts": datetime.now(UTC),
        "long_rofex_long_lecap": long_lecap,
        "short_rofex_long_dlk": short_dlk,
    }


def _build_long_lecap(
    lecaps: list[dict],
    fut_by_ym: dict[tuple[int, int], dict],
    px_map: dict[str, float],
    spot: float | None,
    today: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c in lecaps:
        vto = _to_date(c.get("fecha_vencimiento"))
        if not vto or vto <= today:
            continue
        fut = fut_by_ym.get((vto.year, vto.month))
        if not fut:
            continue
        fvto = _parse_yyyymmdd(fut.get("vencimiento"))
        px_tf = px_map.get(c.get("ticker", ""))
        px_fut = fut.get("last_price")
        cobro = c.get("flujo_vencimiento")
        plazo = (vto - today).days
        # Descalce = días que la LECAP cobra ANTES (positivo) o DESPUÉS
        # (negativo) que el futuro. En matches naturales (ambos a fin de mes)
        # da 0. Si la LECAP es mid-month, queda exposición ARS por unos días.
        descalce = (fvto - vto).days if fvto else None

        t0 = _div(px_tf, spot)
        tn = _div(cobro, px_fut)
        te = (tn / t0 - 1) if (t0 and tn) else None
        tna = _annualize_lin(te, plazo)

        rows.append({
            "ticker":            c.get("ticker_corto"),
            "ticker_largo":      c.get("ticker"),
            "futuro_ticker":     fut.get("ticker"),
            "px_tf":             px_tf,
            "px_futuro":         px_fut,
            "vto_fecha":         vto.isoformat(),
            "futuro_vto_fecha":  fvto.isoformat() if fvto else None,
            "cobro":             cobro,
            "plazo_normal":      plazo,
            "descalce":          descalce,
            "t0":                t0,
            "tn":                tn,
            "te":                te,
            "tna":               tna,
        })
    rows.sort(key=lambda r: r["vto_fecha"] or "")
    return rows


def _build_short_dlk(
    dlks: list[dict],
    fut_by_ym: dict[tuple[int, int], dict],
    px_map: dict[str, float],
    spot: float | None,
    today: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c in dlks:
        vto = _to_date(c.get("fecha_vencimiento"))
        if not vto or vto <= today:
            continue
        fut = fut_by_ym.get((vto.year, vto.month))
        if not fut:
            continue
        fvto = _parse_yyyymmdd(fut.get("vencimiento"))
        px_dlk = px_map.get(c.get("ticker", ""))
        px_fut = fut.get("last_price")
        plazo = (vto - today).days
        descalce = (fvto - vto).days if fvto else None

        te = (px_fut / px_dlk - 1) if (px_dlk and px_fut) else None
        tna = _annualize_lin(te, plazo)

        rows.append({
            "ticker":         c.get("ticker_corto"),
            "ticker_largo":   c.get("ticker"),
            "futuro_ticker":  fut.get("ticker"),
            "px_dlk":         px_dlk,
            "px_futuro":      px_fut,
            "dlr_ajuste":     spot,
            "vto_dlk":        vto.isoformat(),
            "vto_futuro":     fvto.isoformat() if fvto else None,
            "plazo_normal":   plazo,
            "descalce":       descalce,
            "te":             te,
            "tna":            tna,
        })
    rows.sort(key=lambda r: r["vto_dlk"] or "")
    return rows
