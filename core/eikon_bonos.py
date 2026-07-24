"""Feed Eikon — precio OFFSHORE de los soberanos ARG (la pata que operan los
inversores extranjeros, páginas contribuidas tipo MarketAxess).

Mismo riel que Chicago (`core/eikon_chicago.py`): el MISMO script de oficina
suscribe los RICs y postea crudo a `POST /api/ingest/eikon/bonos/quotes`; la API
persiste en SQL `mercado.eikon_bonos_snapshot` (1 fila por RIC, heartbeat).

Consumers:
  - Watchlist HOME sección ARGENTINA (`api/services/argy.py` agrega las filas
    "GD30 OFF" — el front las renderiza solo).
  - Modal de briefing (`api/services/briefing.py`, bloque `bonos_off`).

Los RICs "=1M" son páginas de contribuidor (no exchange): qué campos publican
NO está verificado en vivo (REGLA #2) — por eso el feed pide un set tolerante
(CF_LAST/PRIMACT_1/CF_BID/CF_ASK/PCTCHNG/NETCHNG_1) y el precio se resuelve acá
con cadena de fallback: last → primact → mid(bid,ask). Mapeo RIC→bono provisto
por el user (watchlist MarketAxess, 2026-07-24).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.pg_mirror import write_native
from core.postgres import get_pool

TABLE = "mercado.eikon_bonos_snapshot"
ONLINE_TTL_S = 180   # mismo criterio que Chicago (heartbeat del mismo loop)

# RIC de la página offshore → ticker local del bono. El sufijo " OFF" del label
# lo arma el reader (los consumers muestran "GD29 OFF").
BONOS_OFF: dict[str, str] = {
    # Globales (ley extranjera, CUSIP 040114H…)
    "040114HX1=1M": "GD29",
    "040114HS2=1M": "GD30",
    "040114HT0=1M": "GD35",
    "040114HU7=1M": "GD38",
    "040114HV5=1M": "GD41",
    "040114HW3=1M": "GD46",
    # Bonares (ley local, ISIN ARARGE3209…)
    "ARARGE3209Y=1M": "AL29",
    "ARARGE3209S=1M": "AL30",
    "ARARGE3209T=1M": "AL35",
    "ARARGE3209U=1M": "AE38",
    "ARARGE3209V=1M": "AL41",
}


def universo_bonos_off() -> list[dict]:
    """Lista de suscripción del feed: [{ric, bono}]. Constante — sin catálogo."""
    return [{"ric": ric, "bono": b} for ric, b in BONOS_OFF.items()]


def upsert_bonos_off(docs: list[dict]) -> int:
    """Upsertea quotes del feed (crudos). Solo RICs del universo conocido.
    `updated_at` lo pone el server (mismo contrato que chicago/quotes)."""
    if not docs:
        return 0
    now = datetime.now(UTC)
    rows: list[dict] = []
    for data in docs:
        if not isinstance(data, dict):
            continue
        ric = (data.get("ric") or "").strip()
        if ric not in BONOS_OFF:
            continue
        rows.append({"ric": ric, "bono": BONOS_OFF[ric], "data": data, "updated_at": now})
    if not rows:
        return 0
    return write_native(TABLE, ["ric"], rows)


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def precio_off(data: dict) -> float | None:
    """Precio de la página offshore con cadena de fallback: last → primact →
    mid(bid, ask). None si no hay ninguna pata (celda vacía, nunca rompe)."""
    last = _num(data.get("last"))
    if last:
        return last
    primact = _num(data.get("primact"))
    if primact:
        return primact
    bid, ask = _num(data.get("bid")), _num(data.get("ask"))
    if bid and ask:
        return (bid + ask) / 2
    return bid or ask


def filas_bonos_off() -> list[dict]:
    """Filas para watchlist/briefing: [{bono, label, precio, var_pct, updated_at,
    online}] en el orden de BONOS_OFF. Solo RICs con snapshot. Nunca rompe: si
    la tabla no existe o está vacía → []."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT ric, data, updated_at FROM {TABLE}")
            snap = {ric: (data or {}, upd) for ric, data, upd in cur.fetchall()}
    except Exception:
        return []
    now = datetime.now(UTC)
    out = []
    for ric, bono in BONOS_OFF.items():
        if ric not in snap:
            continue
        data, upd = snap[ric]
        out.append({
            "bono":       bono,
            "label":      f"{bono} OFF",
            "precio":     precio_off(data),
            "var_pct":    _num(data.get("var_pct")),
            "updated_at": upd,
            "online":     bool(upd and now - upd < timedelta(seconds=ONLINE_TTL_S)),
        })
    return out
