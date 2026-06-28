"""Service — Mejoras Precio Disponible (Agro).

Replica la planilla que arma la mesa para proponerle al productor:
*"En vez de pagarte hoy en pesos, te colocás en una LECAP a X días → al
vto cobrás capital + interés, y si encima cubrís con un futuro DLR del
mismo mes, te llevás esos pesos a USD locked."*

Tres bloques (SOJA / MAIZ / TRIGO). Para cada uno, una fila por cada
LECAP / BONCAP vigente:

    tasa_directa   = TNA × días / 365             (lineal)
    tasa_diaria    = (1 + tasa_directa)^(1/días) − 1
    interes_ganado = precio_ars × tasa_directa
    valor_final    = precio_ars + interes_ganado
    valor_usd      = valor_final / Px_Futuro_DLR_del_mismo_mes
                      (None si descalce > MAX_DESCALCE_DIAS)

`precio_ars` viene de **Derivados.CamaraCereales** (input manual del trader).
La TNA viene de `Trading.MarketSnapshot.metrics.TEA` (la calcula el motor
de curvas) — la desk lo llama "TNA" pero es la TIR efectiva anual.

Cache 5s. Match LECAP ↔ futuro DLR por (año, mes), igual que en `sinteticos`.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from api.cache import cached
from core import curvas_sql
from core.dolar_oficial import mid_oficial_live
from core.mongo import get_mongo_client_read

# Solo estos 3 commodities matchean con la cosecha local + futuros DLR.
# GIRASOL y SORGO existen en la Cámara pero por ahora no se proponen.
COMMODITIES = ("SOJA", "MAIZ", "TRIGO")

# Descalce máximo (días entre vto LECAP y vto futuro DLR) para mostrar el
# valor en US$. Por encima de eso la cobertura tiene gap operativo y la
# planilla pone N/A.
MAX_DESCALCE_DIAS = 10


def _parse_yyyymmdd(s: str | None) -> date | None:
    if not s or len(s) != 8:
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (TypeError, ValueError):
        return None


def _to_date(d: Any) -> date | None:
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


@cached(ttl=5)
def get_mejoras_dispo() -> dict[str, Any]:
    """Devuelve los 3 bloques. Cache 5s."""
    db = get_mongo_client_read()
    trading = db["Trading"]
    derivados = db["Derivados"]
    today = date.today()
    hoy_str = today.strftime("%Y%m%d")

    spot = mid_oficial_live("oficial").get("value")

    # Cámara — precio_ars por cereal.
    camara_docs = {d["_id"]: d for d in derivados["CamaraCereales"].find({})}

    # Futuros DLR vigentes indexados por (año, mes).
    fut_by_ym: dict[tuple[int, int], dict] = {}
    for f in trading["FuturosDLRSnapshot"].find(
        {"vencimiento": {"$gt": hoy_str}}, {"_id": 0}
    ):
        fvto = _parse_yyyymmdd(f.get("vencimiento"))
        if fvto:
            fut_by_ym.setdefault((fvto.year, fvto.month), f)

    # LECAPs con flujo_vencimiento (deja afuera CER fijado y similares). Master
    # desde SQL mercado.curvas; el filtro flujo_vencimiento not-null se aplica en
    # Python sobre el doc completo (no mapea a columna).
    lecaps = [c for c in curvas_sql.por_curva("tasa_fija")
              if c.get("flujo_vencimiento") is not None]

    # TEA (la "TNA" de la mesa) de cada LECAP en una sola query.
    lecap_tickers = [c["ticker"] for c in lecaps if c.get("ticker")]
    tea_map: dict[str, float] = {}
    if lecap_tickers:
        from core.market_snapshot import metric_map
        tea_map = metric_map(lecap_tickers, "tea")  # SQL-only (mercado.market_snapshot)

    bloques = []
    for commodity in COMMODITIES:
        cam = camara_docs.get(commodity) or {}
        precio_ars = cam.get("precio_ars")
        filas = _build_filas(lecaps, tea_map, fut_by_ym, precio_ars, today)
        bloques.append({
            "commodity":  commodity,
            "precio_ars": precio_ars,
            "filas":      filas,
            "precio_updated_at": cam.get("updated_at"),
            "precio_updated_by": cam.get("updated_by"),
        })

    return {
        "ts":      datetime.now(UTC),
        "spot":    spot,
        "bloques": bloques,
    }


def _build_filas(
    lecaps: list[dict],
    tea_map: dict[str, float],
    fut_by_ym: dict[tuple[int, int], dict],
    precio_ars: float | None,
    today: date,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in lecaps:
        vto = _to_date(c.get("fecha_vencimiento"))
        if not vto or vto <= today:
            continue
        dias = (vto - today).days
        if dias <= 0:
            continue
        tna = tea_map.get(c.get("ticker", ""))

        # Tasa lineal al vto (sobre los días). Si falta TNA, las derivadas
        # quedan en None — pero la fila igual aparece, para que el trader
        # vea el universo completo.
        tasa_directa = (tna * dias / 365) if tna is not None else None
        tasa_diaria = (
            (1 + tasa_directa) ** (1 / dias) - 1
            if tasa_directa is not None and dias > 0
            else None
        )

        interes = (
            precio_ars * tasa_directa
            if (precio_ars is not None and tasa_directa is not None)
            else None
        )
        valor_final = (
            precio_ars + interes
            if (precio_ars is not None and interes is not None)
            else None
        )

        # Match con futuro DLR del mismo mes — descalce > MAX → None.
        fut = fut_by_ym.get((vto.year, vto.month))
        fvto = _parse_yyyymmdd(fut.get("vencimiento")) if fut else None
        descalce = abs((fvto - vto).days) if fvto else None
        px_fut = fut.get("last_price") if fut else None
        if (
            valor_final is not None
            and px_fut
            and descalce is not None
            and descalce <= MAX_DESCALCE_DIAS
        ):
            valor_usd = valor_final / px_fut
        else:
            valor_usd = None

        out.append({
            "ticker":         c.get("ticker_corto"),
            "ticker_largo":   c.get("ticker"),
            "vencimiento":    vto.isoformat(),
            "dias":           dias,
            "tna":            tna,
            "tasa_diaria":    tasa_diaria,
            "tasa_directa":   tasa_directa,
            "interes_ganado": interes,
            "valor_final":    valor_final,
            "futuro_ticker":  fut.get("ticker") if fut else None,
            "futuro_px":      px_fut,
            "descalce":       descalce,
            "valor_usd":      valor_usd,
        })
    out.sort(key=lambda r: r["vencimiento"])
    return out
