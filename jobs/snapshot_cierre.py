"""snapshot_cierre.py — materializa el cierre diario por bono en Trading.SnapshotsCierre.

Llama a `snapshot_curva_historico(curva, fecha=hoy)` (que ya existe y agrega
TimeSales por ticker tomando el último trade del día) y le agrega:
  - total_nominals_dia: viene de Trading.MarketSnapshot.metrics.total_nominals
    (acumulado intra-día que el motor ya tenía en memoria al cierre).
  - fecha_emision: viene de Trading.Curvas (metadata estática).

El motor de mercado para a 17:05 ART; este cron corre 17:25 ART (20:25 UTC),
así que TimeSales y MarketSnapshot ya no reciben más writes del día.

IDEMPOTENTE: re-correr el mismo día (con TimeSales ya quieto) produce los
mismos docs. Upsert por (ts_cierre, curva, ticker).

Uso:
    python -m jobs.snapshot_cierre              # cierre del día UTC actual
    python -m jobs.snapshot_cierre --fecha 2026-04-25
    python -m jobs.snapshot_cierre --dry        # no persiste, imprime resumen
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, date, datetime

from api.services.analitica import snapshot_curva_historico
from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("SnapshotCierre")

# Para V1 valuamos solo tasa fija + CER. Soberanos quedan para V2 (requieren
# splittear globales/bonares por jurisdicción, fuera de scope acá).
CURVAS_V1 = ("tasa_fija", "cer")


def _volumen_por_ticker(client, tickers: list[str]) -> dict[str, float]:
    """Lee total_nominals acumulado del día desde MarketSnapshot.

    MarketSnapshot tiene 1 doc por ticker, escrito cada 1s por engines/valores.py.
    El campo `metrics.total_nominals` es un contador acumulado intra-día del
    motor (se reinicia a las 0 del día siguiente). Al correr post-cierre,
    representa el volumen total del día.
    """
    out: dict[str, float] = {}
    cur = client["Trading"]["MarketSnapshot"].find(
        {"ticker": {"$in": tickers}},
        {"_id": 0, "ticker": 1, "metrics.total_nominals": 1},
    )
    for d in cur:
        v = (d.get("metrics") or {}).get("total_nominals")
        if v is not None:
            try:
                out[d["ticker"]] = float(v)
            except (TypeError, ValueError):
                pass
    return out


def _meta_curvas(client, curva: str) -> dict[str, dict]:
    """Lee metadata estática de Trading.Curvas: ticker → {ticker_corto, fecha_emision, cupon_anual}."""
    out: dict[str, dict] = {}
    cur = client["Trading"]["Curvas"].find(
        {"curva": curva},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "fecha_emision": 1, "cupon_anual": 1},
    )
    for d in cur:
        if d.get("ticker"):
            out[d["ticker"]] = d
    return out


def _norm_fecha(v) -> str | None:
    """Normaliza fecha (datetime o str) a 'YYYY-MM-DD' o None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    s = str(v)
    return s[:10] if len(s) >= 10 else None


def procesar_curva(client, curva: str, fecha_str: str, dry: bool) -> int:
    """Procesa una curva. Devuelve cantidad de docs upserteados."""
    snap = snapshot_curva_historico(curva=curva, fecha=fecha_str)
    if not snap:
        logger.warning("[%s] snapshot vacío para %s — saltando", curva, fecha_str)
        return 0

    tickers = [r["ticker"] for r in snap]
    volumenes = _volumen_por_ticker(client, tickers)
    metas = _meta_curvas(client, curva)

    col = client["Trading"]["SnapshotsCierre"]
    n_ok = 0
    for r in snap:
        ticker = r["ticker"]
        meta = metas.get(ticker, {})
        doc = {
            "ts_cierre":        fecha_str,
            "curva":            curva,
            "ticker":           ticker,
            "ticker_corto":     r.get("ticker_corto"),
            "tipo":             r.get("tipo"),
            "fecha_vencimiento": r.get("fecha_vencimiento"),
            "fecha_emision":    _norm_fecha(meta.get("fecha_emision")),
            "ultimo_precio":    r.get("ultimo_precio"),
            "tea":              r.get("tea"),
            "tem":              r.get("tem"),
            "paridad":          r.get("paridad"),
            "duration":         r.get("duration"),
            "mod_duration":     r.get("mod_duration"),
            "convexity":        r.get("convexity"),
            "total_nominals_dia": volumenes.get(ticker),
            "is_zero_coupon":   r.get("is_zero_coupon"),
        }
        if dry:
            n_ok += 1
            continue
        col.update_one(
            {"ts_cierre": fecha_str, "curva": curva, "ticker": ticker},
            {"$set": doc},
            upsert=True,
        )
        n_ok += 1

    logger.info("[%s %s] %d bonos persistidos", curva, fecha_str, n_ok)
    return n_ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fecha", help="YYYY-MM-DD (default: hoy UTC)")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    if args.fecha:
        try:
            fecha_d = date.fromisoformat(args.fecha)
        except ValueError:
            raise SystemExit(f"--fecha inválida: {args.fecha}") from None
    else:
        fecha_d = datetime.now(UTC).date()
    fecha_str = fecha_d.isoformat()

    client = get_mongo_client()

    # Indice único idempotente. Mongo lo crea solo la primera vez.
    client["Trading"]["SnapshotsCierre"].create_index(
        [("ts_cierre", 1), ("curva", 1), ("ticker", 1)],
        unique=True,
        name="uq_ts_curva_ticker",
    )

    total = 0
    for curva in CURVAS_V1:
        total += procesar_curva(client, curva, fecha_str, args.dry)

    logger.info("Total: %d docs en %s", total, fecha_str)
    if args.dry:
        logger.info("(--dry: no se escribió en Mongo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
