"""
main_breakevens.py — Motor de breakevens CER/Lecap en tiempo real.

Cada 30s:
  1. Lee última TEM por Lecap y última paridad por CER desde Trading.TimeSales
  2. Empareja cada Lecap con el CER de vencimiento más cercano (Trading.Curvas)
  3. Calcula breakeven de inflación mensual implícita
  4. Upsert Trading.BreakevensLive  → 1 doc global (tiempo real)
  5. Upsert Trading.BreakevensHistorico → 1 doc por fecha (histórico diario)

Fórmulas:
  retorno_acumulado   = (1 + TEM)^(días/30) - 1
  inflacion_acumulada = (1 + retorno_acumulado) * (paridad/100) - 1
  breakeven_mensual   = (1 + inflacion_acumulada)^(30/días) - 1

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/main_breakevens.py
"""

import time
import logging
import traceback
from datetime import datetime, date

from mongo_manager import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorBreakevens")

INTERVALO = 30          # segundos entre corridas
MAX_DIFF_DIAS = 60      # umbral para emparejar Lecap↔CER por vencimiento


# ─────────────────────────────────────────────
# Carga y emparejamiento de instrumentos
# ─────────────────────────────────────────────

def cargar_pares(client):
    """
    Lee Trading.Curvas y devuelve lista de pares (Lecap, CER) ordenados por vencimiento.
    Cada Lecap se empareja con el CER cuya fecha_vencimiento es más cercana.
    Solo se incluyen pares con diferencia ≤ MAX_DIFF_DIAS días.
    """
    docs = list(client["Trading"]["Curvas"].find({}))
    lecaps = [d for d in docs if d.get("curva") == "tasa_fija"]
    cers   = [d for d in docs if d.get("curva") == "cer"]

    pares = []
    for lecap in lecaps:
        try:
            fecha_lec = date.fromisoformat(lecap["fecha_vencimiento"][:10])
        except Exception:
            continue

        mejor, mejor_diff = None, None
        for cer in cers:
            try:
                fecha_cer = date.fromisoformat(cer["fecha_vencimiento"][:10])
            except Exception:
                continue
            diff = abs((fecha_lec - fecha_cer).days)
            if mejor_diff is None or diff < mejor_diff:
                mejor_diff = diff
                mejor = cer

        if mejor is None or mejor_diff > MAX_DIFF_DIAS:
            continue

        pares.append({
            "lecap_ticker": lecap["ticker"],
            "lecap_corto":  lecap["ticker_corto"],
            "cer_ticker":   mejor["ticker"],
            "cer_corto":    mejor["ticker_corto"],
            "fecha_vencimiento": lecap["fecha_vencimiento"][:10],
        })

    pares.sort(key=lambda p: p["fecha_vencimiento"])
    logger.info(f"Pares CER/Lecap cargados: {len(pares)}")
    return pares


# ─────────────────────────────────────────────
# Lectura de mercado desde TimeSales
# ─────────────────────────────────────────────

def obtener_tems(client, tickers):
    """Última TEM por ticker Lecap."""
    pipeline = [
        {"$match": {"ticker": {"$in": tickers}, "TEM": {"$exists": True}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": "$ticker", "TEM": {"$first": "$TEM"}}},
    ]
    return {r["_id"]: r["TEM"] for r in client["Trading"]["TimeSales"].aggregate(pipeline)}


def obtener_paridades(client, tickers):
    """Última paridad por ticker CER."""
    pipeline = [
        {"$match": {"ticker": {"$in": tickers}, "paridad": {"$exists": True}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": "$ticker", "paridad": {"$first": "$paridad"}}},
    ]
    return {r["_id"]: r["paridad"] for r in client["Trading"]["TimeSales"].aggregate(pipeline)}


# ─────────────────────────────────────────────
# Cálculo de breakevens
# ─────────────────────────────────────────────

def calcular_breakevens(pares, tems, paridades, fecha_ref):
    """
    fecha_ref: date — se usa para calcular días a vencimiento.
    Devuelve lista de dicts con los resultados.
    """
    resultado = []
    for i, par in enumerate(pares, 1):
        try:
            fecha_vto = date.fromisoformat(par["fecha_vencimiento"])
            dias = (fecha_vto - fecha_ref).days
        except Exception:
            continue

        if dias <= 0:
            continue

        entry = {
            "n":                 i,
            "lecap":             par["lecap_corto"],
            "cer":               par["cer_corto"],
            "fecha_vencimiento": par["fecha_vencimiento"],
            "dias":              dias,
        }

        tem     = tems.get(par["lecap_ticker"])
        paridad = paridades.get(par["cer_ticker"])

        if tem is not None:
            entry["tem_lecap"] = round(float(tem), 6)

        if paridad is not None:
            entry["paridad_cer"] = round(float(paridad), 4)

        if tem is not None and paridad is not None:
            try:
                retorno    = (1 + float(tem)) ** (dias / 30) - 1
                inflacion  = (1 + retorno) * (float(paridad) / 100) - 1
                bkv        = (1 + inflacion) ** (30 / dias) - 1
                if -0.5 < bkv < 10:
                    entry["retorno_acumulado"]   = round(retorno, 6)
                    entry["inflacion_acumulada"] = round(inflacion, 6)
                    entry["breakeven_mensual"]   = round(bkv, 6)
            except Exception:
                pass

        resultado.append(entry)

    return resultado


# ─────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────

def guardar(client, pares_result, ts, fecha_str):
    if not pares_result:
        return

    doc_base = {
        "updated_at": ts,
        "pares":      pares_result,
    }

    # BreakevensLive: 1 doc global, siempre pisado
    client["Trading"]["BreakevensLive"].update_one(
        {"_id": "breakevens"},
        {"$set": doc_base},
        upsert=True,
    )

    # BreakevensHistorico: 1 doc por fecha, actualizado durante el día
    client["Trading"]["BreakevensHistorico"].update_one(
        {"fecha": fecha_str},
        {"$set": {**doc_base, "fecha": fecha_str}},
        upsert=True,
    )


# ─────────────────────────────────────────────
# Loop principal
# ─────────────────────────────────────────────

def run():
    logger.info("Motor Breakevens iniciando...")
    client = get_mongo_client()

    pares         = cargar_pares(client)
    lecap_tickers = [p["lecap_ticker"] for p in pares]
    cer_tickers   = [p["cer_ticker"]   for p in pares]

    logger.info(f"Monitoreando {len(pares)} pares Lecap/CER.")

    while True:
        try:
            ts         = datetime.now()
            fecha_ref  = ts.date()
            fecha_str  = fecha_ref.isoformat()

            tems      = obtener_tems(client, lecap_tickers)
            paridades = obtener_paridades(client, cer_tickers)

            pares_result = calcular_breakevens(pares, tems, paridades, fecha_ref)
            guardar(client, pares_result, ts, fecha_str)

            n_completos = sum(1 for p in pares_result if "breakeven_mensual" in p)
            logger.info(f"{len(pares_result)} pares | {n_completos} con breakeven calculado.")

        except Exception:
            logger.error(f"Error en loop:\n{traceback.format_exc()}")

        time.sleep(INTERVALO)


if __name__ == "__main__":
    run()
