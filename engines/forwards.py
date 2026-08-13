"""
main_forwards.py — Motor de tasas forward en tiempo real.

Cada 30s:
  1. Lee última TEA por ticker desde Trading.TimeSales
  2. Agrupa por curva (Trading.Curvas)
  3. Calcula matriz NxN de tasas forward
  4. Upsert SQL mercado.mercado_hist (coleccion='ForwardsHistorico', k=curva) →
     1 fila por (fecha, curva) (histórico diario; la fila de HOY se reescribe en
     cada corrida). El "live" por curva es la fila más reciente — ya no hay
     una tabla de estado aparte.

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/main_forwards.py
"""

import logging
import time
import traceback
from datetime import UTC, datetime

from engines._curvas_loader import cargar_por_curva

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorForwards")

INTERVALO = 30  # segundos


# ─────────────────────────────────────────────
# Carga de referencia
# ─────────────────────────────────────────────

def obtener_ultimas_teas(tickers):
    """
    Devuelve la última TEA y duration disponibles por ticker.
    { ticker: {"TEA": float, "duration": float} }

    SQL-only: mercado.market_snapshot (tea/duration, escrito por curvas.py).
    """
    from core.market_snapshot import cols_map
    out = {}
    for ticker, m in cols_map(tickers, ["tea", "duration"]).items():
        if m["tea"] is not None and m["duration"] is not None:
            out[ticker] = {"TEA": m["tea"], "duration": m["duration"]}
    return out


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

    if not validos:
        return [], {}, {}

    ordered = [v["ticker_corto"] for v in validos]
    tasas = {v["ticker_corto"]: round(v["TEA"], 6) for v in validos}

    # Con 1 solo bono no hay forwards que calcular — publicamos las tasas
    # spot igualmente para que la UI pueda mostrar la TEA en la tabla de
    # renta fija. La matrix queda vacía hasta que entre un segundo bono.
    if len(validos) < 2:
        return ordered, tasas, {}

    matrix: dict = {}

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

def guardar(curva, ordered, tasas, matrix, ts, fecha_str):
    if not ordered:
        return

    doc_base = {
        "curva":     curva,
        "updated_at": ts,
        "tickers":   ordered,   # orden por maturity
        "tasas":     tasas,     # spot TEA por ticker
        "matrix":    matrix,    # forwards NxN
    }

    # SQL-NATIVE (decomiso Mongo 2026-06-28): el único write es a mercado_hist
    # (coleccion='ForwardsHistorico', k=curva). ForwardsLive (Mongo) ya no se
    # escribe — el reader live (mercado_hist_sql.get_forwards) toma la fila MÁS
    # RECIENTE por curva. El doc es IDÉNTICO al que escribía el sync desde Mongo
    # (curva, updated_at, tickers, tasas, matrix, fecha).
    from core.pg_mirror import write_hist
    write_hist("ForwardsHistorico", fecha_str, curva, {**doc_base, "fecha": fecha_str})


# ─────────────────────────────────────────────
# Loop principal
# ─────────────────────────────────────────────

def run():
    logger.info("Motor Forwards iniciando...")
    # SQL-NATIVE (decomiso Mongo): el motor no lee ni escribe Mongo. La TEA/duration
    # salen de SQL (market_snapshot) y el write va a mercado_hist. `obtener_ultimas_teas`
    # aún acepta `client` por firma histórica pero lo IGNORA → se le pasa None.

    grupos = cargar_por_curva()
    logger.info(f"Curvas cargadas: {list(grupos.keys())}")
    todos_tickers = [
        inst["ticker"]
        for insts in grupos.values()
        for inst in insts
        if inst.get("ticker")
    ]

    logger.info(f"Monitoreando {len(todos_tickers)} tickers en {len(grupos)} curvas.")

    while True:
        try:
            ts = datetime.now(UTC)
            fecha_str = ts.date().isoformat()

            tasas_tea = obtener_ultimas_teas(todos_tickers)

            for curva, instrumentos in grupos.items():
                ordered, tasas, matrix = calcular_matriz(instrumentos, tasas_tea)
                if ordered:
                    guardar(curva, ordered, tasas, matrix, ts, fecha_str)
                    logger.info(f"[{curva}] {len(ordered)} instrumentos | matrix {len(matrix)}x{len(ordered)}")

        except Exception:
            logger.error(f"Error en loop:\n{traceback.format_exc()}")

        time.sleep(INTERVALO)


if __name__ == "__main__":
    run()
