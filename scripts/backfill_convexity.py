"""backfill_convexity.py — agregar `convexity` a docs históricos de Trading.TimeSales.

Contexto: convexity se persiste desde commit 0a5ad25 (2026-04-21). Trades
anteriores ya estaban enriquecidos con TEA/duration por la versión vieja del
motor, así que el filtro de motor_curvas (`duration: {$exists: false}`) no los
toca y se quedan sin convexity. Este script rellena ese hueco.

Idempotente: filtra `convexity: {$exists: false}`, correr 2 veces no duplica.

Uso:
    python -m scripts.backfill_convexity              # corre todo
    python -m scripts.backfill_convexity --dry        # solo cuenta candidatos
    python -m scripts.backfill_convexity --ticker T   # acota a un ticker
    python -m scripts.backfill_convexity --limit 1000 # tope total de docs
    python -m scripts.backfill_convexity --batch 5000 # tamaño de batch

Por qué no necesita CER ni MEP:
    convexity es escala-invariante. Si f_i son los flujos y r una constante
    (ej. ratio CER cer_liq/cer_emision o el factor MEP):
        convexity(r·f, y) = Σ t(t+1)·r·f_i / (1+y)^(t+2)
                            ─────────────────────────────────  = igual a f
                            Σ r·f_i / (1+y)^t_i
    El r se cancela. Por eso se computa con flujos en % (CER) o USD nominal
    (soberanos) sin escalar, usando el TEA YA persistido en el doc — así el
    valor queda consistente con el yield que el doc reporta.

Solo `$set: {convexity: ...}` — NO toca TEA / TEM / duration / paridad.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime

from pymongo import UpdateOne

from core.mongo import get_mongo_client
from engines._curvas_loader import cargar_indexado_por_ticker
from engines.curvas import (
    cargar_dias_habiles,
    convexity,
    fecha_flujo,
    monto_flujo,
    monto_flujo_cer,
    monto_flujo_soberano,
    siguiente_dia_habil,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("backfill_convexity")

CURVAS_CON_CONVEXITY = ("tasa_fija", "cer", "soberanos")


def calcular_convexity_doc(
    doc: dict, instrumento: dict, dias_habiles: list[str],
) -> float | None:
    """Recalcula convexity reusando el TEA del doc. None si no se puede."""
    tea = doc.get("TEA")
    if tea is None:
        return None

    timestamp = doc.get("timestamp")
    if not isinstance(timestamp, datetime):
        return None
    fecha_trade = timestamp.date()

    fecha_vto_str = instrumento.get("fecha_vencimiento")
    if not fecha_vto_str:
        return None
    try:
        fecha_vto = date.fromisoformat(fecha_vto_str[:10])
    except Exception:
        return None
    if (fecha_vto - fecha_trade).days <= 0:
        return None

    curva = instrumento.get("curva")
    flujos_raw = instrumento.get("flujos") or []
    flujo_vto = instrumento.get("flujo_vencimiento")
    valor_nominal = float(instrumento.get("valor_nominal", 100))

    # Resolver fecha_base + flujos_futuros según el tipo de curva.
    if curva == "tasa_fija":
        fecha_base = fecha_trade
        if flujos_raw:
            flujos_futuros = [
                (fecha_flujo(f), monto_flujo(f))
                for f in flujos_raw
                if fecha_flujo(f) and fecha_flujo(f) > fecha_trade and monto_flujo(f) > 0
            ]
        elif flujo_vto and flujo_vto > 0:
            # Zero coupon: único flujo al vto.
            flujos_futuros = [(fecha_vto, float(flujo_vto))]
        else:
            return None

    elif curva == "cer":
        settlement_str = siguiente_dia_habil(dias_habiles, fecha_trade)
        if not settlement_str:
            return None
        fecha_settlement = date.fromisoformat(settlement_str)
        if (fecha_vto - fecha_settlement).days <= 0:
            return None
        fecha_base = fecha_settlement
        flujos_futuros = [
            (fecha_flujo(f), monto_flujo_cer(f, valor_nominal))
            for f in flujos_raw
            if fecha_flujo(f)
            and fecha_flujo(f) > fecha_settlement
            and monto_flujo_cer(f, valor_nominal) > 0
        ]

    elif curva == "soberanos":
        settlement_str = siguiente_dia_habil(dias_habiles, fecha_trade)
        fecha_settlement = (
            date.fromisoformat(settlement_str) if settlement_str else fecha_trade
        )
        fecha_base = fecha_settlement
        flujos_futuros = [
            (fecha_flujo(f), monto_flujo_soberano(f, valor_nominal))
            for f in flujos_raw
            if fecha_flujo(f)
            and fecha_flujo(f) > fecha_settlement
            and monto_flujo_soberano(f, valor_nominal) > 0
        ]

    else:
        return None

    if not flujos_futuros:
        return None

    fechas_dt = [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
    montos = [m for _, m in flujos_futuros]
    fecha_base_dt = datetime.combine(fecha_base, datetime.min.time())
    return convexity(fechas_dt, montos, tea, fecha_base_dt)


def run(dry: bool, ticker: str | None, batch_size: int, limit: int | None) -> None:
    client = get_mongo_client()
    col_ts = client["Trading"]["TimeSales"]

    curvas = cargar_indexado_por_ticker()
    aplicables = {
        tk: ins for tk, ins in curvas.items()
        if ins.get("curva") in CURVAS_CON_CONVEXITY
    }
    logger.info(
        "%d tickers aplicables (curvas: %s)",
        len(aplicables),
        ", ".join(CURVAS_CON_CONVEXITY),
    )

    if ticker:
        if ticker not in aplicables:
            logger.error(
                "Ticker %s no está en Trading.Curvas con curva en %s",
                ticker, CURVAS_CON_CONVEXITY,
            )
            return
        tickers_iter = [ticker]
    else:
        tickers_iter = list(aplicables.keys())

    dias_habiles = cargar_dias_habiles(client)

    total_proc = 0
    total_upd = 0
    total_skip = 0

    # Iteramos ticker-por-ticker para aprovechar el índice por ticker
    # de TimeSales y no disparar collscans masivos sobre $exists.
    for tk in tickers_iter:
        query = {
            "ticker": tk,
            "convexity": {"$exists": False},
            "duration": {"$exists": True, "$ne": None},
            "TEA": {"$exists": True, "$ne": None},
        }
        n = col_ts.count_documents(query)
        if n == 0:
            continue
        logger.info("[%s] candidatos=%d", tk, n)

        if dry:
            total_proc += n
            continue

        instrumento = aplicables[tk]
        cursor = col_ts.find(
            query, {"_id": 1, "timestamp": 1, "TEA": 1},
        ).batch_size(batch_size)

        ops: list[UpdateOne] = []
        proc = upd = skip = 0
        for doc in cursor:
            doc["ticker"] = tk
            conv = calcular_convexity_doc(doc, instrumento, dias_habiles)
            if conv is None:
                skip += 1
            else:
                ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"convexity": conv}}))
            proc += 1

            if len(ops) >= batch_size:
                col_ts.bulk_write(ops, ordered=False)
                upd += len(ops)
                ops = []

            if limit is not None and (total_proc + proc) >= limit:
                break

        if ops:
            col_ts.bulk_write(ops, ordered=False)
            upd += len(ops)

        logger.info("[%s] proc=%d upd=%d skip=%d", tk, proc, upd, skip)
        total_proc += proc
        total_upd += upd
        total_skip += skip

        if limit is not None and total_proc >= limit:
            logger.info("Limit %d alcanzado, corto.", limit)
            break

    accion = "[dry] candidatos" if dry else "Backfill completo"
    logger.info(
        "%s. proc=%d upd=%d skip=%d",
        accion, total_proc, total_upd, total_skip,
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry", action="store_true", help="Solo cuenta candidatos")
    parser.add_argument("--ticker", help="Limita a un ticker específico")
    parser.add_argument("--batch", type=int, default=5000, help="Batch size (default 5000)")
    parser.add_argument("--limit", type=int, help="Tope total de docs a procesar")
    args = parser.parse_args()
    run(dry=args.dry, ticker=args.ticker, batch_size=args.batch, limit=args.limit)


if __name__ == "__main__":
    main()
