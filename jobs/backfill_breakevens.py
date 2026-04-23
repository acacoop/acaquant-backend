"""Backfill histórico de Trading.BreakevensHistorico.

Reconstruye, para cada día del rango, la tabla de breakevens con la lógica
vigente en engines.breakevens (matching por mismo vto ±20d, label
mes_inflacion = vto − 2m, anualización sobre días hasta liquidación CER
en lugar de días al vto). Sobreescribe docs preexistentes.

Usa `engines.breakevens.cargar_pares()` y `calcular_breakevens()` para
garantizar paridad 100% con el motor live.

Idempotente: upsert por `fecha`. Se saltea hoy por default (lo maneja
el motor live).

Uso típico:

    python -m jobs.backfill_breakevens            # rango autodetectado
    python -m jobs.backfill_breakevens --desde 2026-01-02 --hasta 2026-04-22
    python -m jobs.backfill_breakevens --dry      # preview sin escribir
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from core.mongo import get_mongo_client
from engines.breakevens import (
    calcular_breakevens,
    cargar_dias_habiles,
    cargar_pares,
)


def _parse_fecha(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _cer_max_y_valor_en(client, fecha: date) -> tuple[str | None, float | None]:
    """CER publicado más reciente con fecha ≤ (fecha + 14d corridos), y su
    valor. Devuelve (fecha_iso, valor).

    El BCRA publica CER con ~10 hábiles de forward — por eso el horizonte
    es `fecha + 14d corridos` y no simplemente `fecha`.
    """
    horizonte = (fecha + timedelta(days=14)).isoformat()
    doc = client["Trading"]["CER"].find_one(
        {"fecha": {"$lte": horizonte}},
        sort=[("fecha", -1)],
        projection={"_id": 0, "fecha": 1, "valor": 1},
    )
    if not doc or not doc.get("fecha") or doc.get("valor") is None:
        return None, None
    return str(doc["fecha"])[:10], float(doc["valor"])


def _ultimos_valores_por_dia(
    client,
    tickers_lecap: list[str],
    tickers_cer: list[str],
    desde: date,
    hasta: date,
) -> tuple[dict[date, dict], dict[date, dict], dict[date, dict], dict[date, dict]]:
    """Por cada día del rango: último TEM por Lecap, última paridad+TEA+precio
    por CER, y último precio por Lecap.

    Devuelve (tems, paridades, teas_cer, precios).
    """
    inicio = datetime.combine(desde, datetime.min.time(), tzinfo=UTC)
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    todos_tickers = tickers_lecap + tickers_cer

    def _por_dia(match_extra: dict, campo: str) -> dict[date, dict]:
        pipeline = [
            {"$match": {
                "ticker":    {"$in": todos_tickers},
                "timestamp": {"$gte": inicio, "$lt": fin},
                **match_extra,
            }},
            {"$addFields": {
                "fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            }},
            {"$sort": {"timestamp": -1}},
            {"$group": {"_id": {"ticker": "$ticker", "fecha": "$fecha"},
                        campo: {"$first": f"${campo}"}}},
        ]
        out: dict[date, dict] = defaultdict(dict)
        for r in client["Trading"]["TimeSales"].aggregate(pipeline):
            f = _parse_fecha(r["_id"]["fecha"])
            if f is not None:
                out[f][r["_id"]["ticker"]] = r[campo]
        return out

    tems      = _por_dia({"TEM":     {"$exists": True}},            "TEM")
    paridades = _por_dia({"paridad": {"$exists": True}},            "paridad")
    teas_cer  = _por_dia({"TEA":     {"$exists": True}},            "TEA")
    precios   = _por_dia({"price":   {"$gt": 0}},                   "price")

    return tems, paridades, teas_cer, precios


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desde", help="YYYY-MM-DD (inclusivo)")
    parser.add_argument("--hasta", help="YYYY-MM-DD (inclusivo)")
    parser.add_argument("--incluir-hoy", action="store_true",
                        help="Por default no se reescribe la fecha de hoy.")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    client = get_mongo_client()

    # Pares Lecap/Boncap ↔ CER matcheados por mismo vto. Tomamos los
    # instrumentos vivos hoy; para fechas pasadas donde un bono no estaba
    # activo aún, simplemente no va a tener datos en TimeSales y se saltea.
    pares = cargar_pares()
    if not pares:
        raise SystemExit("No hay pares Lecap/CER cargados — revisá Trading.Curvas.")

    dias_habiles = cargar_dias_habiles(client)
    if not dias_habiles:
        raise SystemExit(
            "Trading.DiasHabiles vacío — corré jobs.dias_habiles primero.",
        )

    tickers_lecap = [p["lecap_ticker"] for p in pares]
    tickers_cer   = [p["cer_ticker"]   for p in pares]

    desde = _parse_fecha(args.desde) if args.desde else None
    hasta = _parse_fecha(args.hasta) if args.hasta else None

    # Autodescubrir rango desde TimeSales si no viene.
    if not desde or not hasta:
        bounds = list(client["Trading"]["TimeSales"].aggregate([
            {"$match": {"ticker": {"$in": tickers_lecap + tickers_cer},
                        "TEM": {"$exists": True}}},
            {"$group": {"_id": None,
                        "min": {"$min": "$timestamp"},
                        "max": {"$max": "$timestamp"}}},
        ]))
        if not bounds:
            raise SystemExit("No hay trades enriquecidos con TEM/paridad.")
        b = bounds[0]
        desde = desde or b["min"].date()
        hasta = hasta or b["max"].date()

    if desde > hasta:
        raise SystemExit(f"--desde {desde} > --hasta {hasta}")

    hoy = datetime.now(UTC).date()
    print(f"Pares:      {len(pares)} (Lecap/Boncap ↔ CER por mismo vto, ±20d)")
    print(f"Rango:      {desde} → {hasta}")
    print(f"Saltea hoy: {'no' if args.incluir_hoy else 'sí'}")

    tems_por_dia, paridades_por_dia, teas_cer_por_dia, precios_por_dia = (
        _ultimos_valores_por_dia(client, tickers_lecap, tickers_cer, desde, hasta)
    )
    fechas_ord = sorted(
        set(tems_por_dia.keys()) | set(paridades_por_dia.keys()),
    )
    if not fechas_ord:
        raise SystemExit("Sin días con datos en el rango.")
    print(f"Días con datos: {len(fechas_ord)} ({fechas_ord[0]} → {fechas_ord[-1]})\n")

    col_hist = client["Trading"]["BreakevensHistorico"]
    nuevos = 0
    pisados = 0
    saltados_hoy = 0
    saltados_sin_pares = 0

    for fecha in fechas_ord:
        if not args.incluir_hoy and fecha == hoy:
            saltados_hoy += 1
            continue

        tems      = tems_por_dia.get(fecha, {})
        paridades = paridades_por_dia.get(fecha, {})
        teas_cer  = teas_cer_por_dia.get(fecha, {})
        precios   = precios_por_dia.get(fecha, {})

        # Para backfill histórico NO filtramos por IPC publicado: queremos
        # reconstruir la foto del mercado TAL COMO ERA ese día. Sí pasamos
        # todo lo que necesita el método Buscar Objetivo (dias_habiles,
        # fecha_cer_max + su valor, precios por bono del día).
        cer_max_dia, cer_actual_dia = _cer_max_y_valor_en(client, fecha)
        resultado = calcular_breakevens(
            pares, tems, paridades, teas_cer, fecha,
            dias_habiles=dias_habiles,
            fecha_cer_max=cer_max_dia,
            precios=precios,
            cer_actual=cer_actual_dia,
        )
        con_bkv = [r for r in resultado if "breakeven_mensual" in r]
        if not con_bkv:
            saltados_sin_pares += 1
            continue

        ts_dia = datetime.combine(fecha, datetime.min.time(), tzinfo=UTC).replace(
            hour=20, minute=0, second=0,
        )
        fecha_str = fecha.isoformat()
        doc = {
            "fecha":      fecha_str,
            "updated_at": ts_dia,
            "pares":      resultado,
        }

        if args.dry:
            nuevos += 1
            if nuevos <= 3:
                print(f"  {fecha_str} → {len(con_bkv)} pares con BE")
            continue

        res = col_hist.update_one(
            {"fecha": fecha_str},
            {"$set": doc},
            upsert=True,
        )
        if res.upserted_id is not None:
            nuevos += 1
        elif res.modified_count > 0:
            pisados += 1

    print()
    print(f"OK: {nuevos} nuevos · {pisados} pisados · "
          f"{saltados_sin_pares} saltados (sin pares con BE) · "
          f"{saltados_hoy} saltados hoy")
    if args.dry:
        print("(--dry: no se escribió en Mongo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
