"""Fuente única para el "dólar oficial" mayorista.

Lee de SQL `valuaciones.dolar_oficial_live` — escrito por el script
`mae_forex.py` que corre en una PC dedicada en la oficina, que pega al
endpoint `POST /api/ingest/dolar-oficial` (la API escribe). El script pollea
api.mae.com.ar cada 30s y upsertea el último precio + variación % del ticker
UST$T (USA Transferencia mayorista A3500).

El feed se persiste en Postgres. La tabla vieja
`Valuaciones.DolarOficialLive` fue migrada → este módulo escribe y lee SOLO
Postgres. Si el script local está caído, `value` queda en None y el frontend
muestra "—". Sin fallback retail (dolarapi.com está ~30 pesos arriba del
mayorista y confundiría a la mesa; se eliminó el 2026-05-04).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from core.pg_mirror import write_native
from core.postgres import get_pool

TABLE = "dolar_oficial_live"            # valuaciones.dolar_oficial_live (search_path)


def upsert_oficial(docs: list[dict]) -> int:
    """Escribe instrumentos MAE en `valuaciones.dolar_oficial_live` (lo usa el
    endpoint de ingesta `POST /api/ingest/dolar-oficial`, alimentado por la PC
    de oficina).

    Upsert por (ticker, codigo_segmento, codigo_plazo) → 1 fila por instrumento
    (no acumula histórico; la lectura toma el más reciente por `updated_at`).
    Acepta cada item como el instrumento crudo de MAE o ya envuelto en
    `{data: {...}}`. `updated_at` lo pone el server (no se confía en el reloj
    del cliente). Devuelve cuántos se escribieron.
    """
    if not docs:
        return 0
    now = datetime.now(UTC)
    rows: list[dict] = []
    for item in docs:
        data = item.get("data") if isinstance(item.get("data"), dict) else item
        if not isinstance(data, dict) or not data.get("ticker"):
            continue
        rows.append({
            "ticker":          data.get("ticker"),
            "codigo_segmento": data.get("codigoSegmento"),
            "codigo_plazo":    data.get("codigoPlazo"),
            "data":            data,          # dict → jsonb
            "updated_at":      now,
        })
    if not rows:
        return 0
    return write_native(TABLE, ["ticker", "codigo_segmento", "codigo_plazo"], rows)


# Filtro EXACTO del dólar oficial mayorista spot (= A3500 / liquidación T+0).
# MAE devuelve muchos instrumentos con `ticker == "UST$T"` (mayorista,
# minorista, T+0, T+1...). El "dólar oficial" que usa la mesa y que liquida
# los futuros DLR es Mayorista (M) plazo 000 (contado, mismo día).
MAE_FILTER_OFICIAL = {"ticker": "UST$T", "codigo_segmento": "M", "codigo_plazo": "000"}


def mid_oficial_live(casa: str = "oficial") -> dict[str, Any]:
    """Spot mayorista vivo desde MAE (SQL).

    Returns:
        {
          "value":     float | None,    # data->>'precioUltimo'
          "variacion": float | None,    # data->>'variacion' (% del día, ya en %)
          "ts":        datetime | None,
          "source":    "mae_local_pc" | "none",
        }

    `casa` queda como parámetro por compat con la API anterior — pero
    MAE solo expone el mayorista oficial. Si `casa != "oficial"`, devuelve none.
    """
    none = {"value": None, "variacion": None, "ts": None, "source": "none"}
    if casa != "oficial":
        return none

    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT data->>'precioUltimo', data->>'variacion', updated_at "
                f"FROM {TABLE} "
                f"WHERE ticker=%s AND codigo_segmento=%s AND codigo_plazo=%s "
                f"ORDER BY updated_at DESC LIMIT 1",
                ("UST$T", "M", "000"),
            )
            row = cur.fetchone()
    except Exception:
        return none

    if not row:
        return none

    px, var, ts = row
    try:
        value = float(px) if px is not None else None
    except (TypeError, ValueError):
        value = None
    try:
        variacion = float(var) if var is not None else None
    except (TypeError, ValueError):
        variacion = None

    return {"value": value, "variacion": variacion, "ts": ts, "source": "mae_local_pc"}
