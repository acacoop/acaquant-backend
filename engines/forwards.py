"""
main_forwards.py — Motor de tasas forward en tiempo real.

Cada 30s:
  1. Lee última TEA por ticker desde Trading.TimeSales
  2. Agrupa por curva (Trading.Curvas)
  3. Calcula matriz NxN de tasas forward
  4. Upsert Trading.ForwardsLive  → 1 doc por curva (tiempo real)
  5. Upsert Trading.ForwardsHistorico → 1 doc por (fecha, curva) (histórico diario)

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/main_forwards.py
"""

import logging
import time
import traceback
from datetime import datetime

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorForwards")

INTERVALO = 30  # segundos


# ─────────────────────────────────────────────
# Carga de referencia
# ─────────────────────────────────────────────

def cargar_curvas(client):
    """Devuelve instrumentos agrupados por curva."""
    docs = list(client["Trading"]["Curvas"].find({}))
    grupos = {}
    for d in docs:
        curva = d.get("curva")
        if not curva:
            continue
        if curva not in grupos:
            grupos[curva] = []
        grupos[curva].append(d)
    logger.info(f"Curvas cargadas: {list(grupos.keys())}")
    return grupos


def obtener_ultimas_teas(client, tickers):
    """
    Devuelve la última TEA y duration disponibles por ticker.
    { ticker: {"TEA": float, "duration": float} }
    """
    pipeline = [
        {"$match": {"ticker": {"$in": tickers}, "TEA": {"$exists": True}, "duration": {"$exists": True}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id":      "$ticker",
            "TEA":      {"$first": "$TEA"},
            "duration": {"$first": "$duration"},
        }},
    ]
    return {r["_id"]: {"TEA": r["TEA"], "duration": r["duration"]} for r in client["Trading"]["TimeSales"].aggregate(pipeline)}


# ─────────────────────────────────────────────
# Cálculo de forwards
# ─────────────────────────────────────────────

def calcular_matriz(instrumentos, tasas_tea):
    """
    Calcula la matriz NxN de tasas forward para un grupo de instrumentos.
    Usa duration (de TimeSales) como plazo efectivo en lugar de días al vencimiento,
    para reflejar correctamente los flujos intermedios de bonos con cupones.

    Retorna:
      - ordered: lista de ticker_corto ordenados por duration (de más corto a más largo)
      - tasas: { ticker_corto: TEA }
      - matrix: { ticker_largo: { ticker_corto: forward_rate } }
               (cada celda = forward desde ticker_corto hasta ticker_largo)
    """
    validos = []

    for inst in instrumentos:
        ticker = inst.get("ticker")
        datos = tasas_tea.get(ticker)
        if datos is None:
            continue
        tea      = datos.get("TEA")
        duration = datos.get("duration")
        if tea is None or duration is None or duration <= 0:
            continue
        validos.append({
            "ticker_corto": inst["ticker_corto"],
            "ticker": ticker,
            "t": duration,
            "TEA": tea,
        })

    # Ordenar por maturity ascendente
    validos.sort(key=lambda x: x["t"])

    if len(validos) < 2:
        return [], {}, {}

    ordered = [v["ticker_corto"] for v in validos]
    tasas = {v["ticker_corto"]: round(v["TEA"], 6) for v in validos}
    matrix = {}

    for i, a in enumerate(validos):
        for j, b in enumerate(validos):
            if j <= i:
                continue  # solo forward desde corto hacia largo
            t_a, t_b = a["t"], b["t"]
            tea_a, tea_b = a["TEA"], b["TEA"]
            dt = t_b - t_a
            if dt <= 0:
                continue
            try:
                forward = ((1 + tea_b) ** t_b / (1 + tea_a) ** t_a) ** (1 / dt) - 1
                if not (-0.5 < forward < 50):
                    continue
                ticker_largo = b["ticker_corto"]
                if ticker_largo not in matrix:
                    matrix[ticker_largo] = {}
                matrix[ticker_largo][a["ticker_corto"]] = round(forward, 6)
            except Exception:
                continue

    return ordered, tasas, matrix


# ─────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────

def guardar(client, curva, ordered, tasas, matrix, ts, fecha_str):
    if not ordered:
        return

    doc_base = {
        "curva":     curva,
        "updated_at": ts,
        "tickers":   ordered,   # orden por maturity
        "tasas":     tasas,     # spot TEA por ticker
        "matrix":    matrix,    # forwards NxN
    }

    col_live = client["Trading"]["ForwardsLive"]
    col_hist = client["Trading"]["ForwardsHistorico"]

    # ForwardsLive: 1 doc por curva, siempre pisado
    col_live.update_one(
        {"curva": curva},
        {"$set": doc_base},
        upsert=True
    )

    # ForwardsHistorico: 1 doc por (fecha, curva), actualizado durante el día
    col_hist.update_one(
        {"curva": curva, "fecha": fecha_str},
        {"$set": {**doc_base, "fecha": fecha_str}},
        upsert=True
    )


# ─────────────────────────────────────────────
# Loop principal
# ─────────────────────────────────────────────

def run():
    logger.info("Motor Forwards iniciando...")
    client = get_mongo_client()

    grupos = cargar_curvas(client)
    todos_tickers = [
        inst["ticker"]
        for insts in grupos.values()
        for inst in insts
        if inst.get("ticker")
    ]

    logger.info(f"Monitoreando {len(todos_tickers)} tickers en {len(grupos)} curvas.")

    while True:
        try:
            ts = datetime.now()
            fecha_str = ts.date().isoformat()

            tasas_tea = obtener_ultimas_teas(client, todos_tickers)

            for curva, instrumentos in grupos.items():
                ordered, tasas, matrix = calcular_matriz(instrumentos, tasas_tea)
                if ordered:
                    guardar(client, curva, ordered, tasas, matrix, ts, fecha_str)
                    logger.info(f"[{curva}] {len(ordered)} instrumentos | matrix {len(matrix)}x{len(ordered)}")

        except Exception:
            logger.error(f"Error en loop:\n{traceback.format_exc()}")

        time.sleep(INTERVALO)


if __name__ == "__main__":
    run()
