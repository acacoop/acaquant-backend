"""
backfill_curvas.py — Script one-off.

Recorre Trading.TimeSales y agrega TEA, TEM y Duration a los docs
cuyos tickers están en Trading.Curvas. No toca ningún otro documento.

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/backfill_curvas.py
"""

import sys
import numpy as np
from datetime import datetime, date, timedelta
from pymongo import UpdateOne
from scipy.optimize import newton

from mongo_manager import get_mongo_client

BATCH_SIZE = 500


# ─────────────────────────────────────────────
# Helpers de cálculo (sin dependencias externas)
# ─────────────────────────────────────────────

def xirr(fechas, flujos):
    """TIR anualizada por Newton-Raphson."""
    dias = np.array([(f - fechas[0]).days for f in fechas], dtype=float)
    anios = dias / 365.0
    cf = np.array(flujos, dtype=float)

    def vpn(tir):
        return np.sum(cf / ((1 + tir) ** anios))

    for semilla in [0.10, 0.40, -0.20]:
        try:
            r = newton(vpn, semilla, tol=1e-6, maxiter=100)
            if -0.99 < r < 50:
                return r
        except RuntimeError:
            continue
    return None


def macaulay_duration(fechas_flujos, montos, tir, fecha_base):
    """Duration de Macaulay en años."""
    t = np.array([(f - fecha_base).days / 365.0 for f in fechas_flujos])
    cf = np.array(montos, dtype=float)
    pv = cf / ((1 + tir) ** t)
    total_pv = np.sum(pv)
    if total_pv <= 0:
        return None
    return round(float(np.sum(t * pv) / total_pv), 4)


def monto_flujo(f):
    """Extrae el monto de un flujo independientemente de su estructura."""
    if "monto" in f:
        return float(f["monto"])
    return float(f.get("amortizacion", 0)) + float(f.get("interes", 0))


def fecha_flujo(f):
    """Parsea la fecha de un flujo (string ISO o datetime)."""
    v = f.get("fecha")
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        return date.fromisoformat(v[:10])
    return None


# ─────────────────────────────────────────────
# Carga de datos de referencia
# ─────────────────────────────────────────────

def cargar_curvas(client):
    docs = list(client["Trading"]["Curvas"].find({}))
    return {d["ticker"]: d for d in docs}


def cargar_cer(client):
    """Devuelve dict { "YYYY-MM-DD": valor_cer }"""
    docs = list(client["Trading"]["CER"].find({}, {"fecha": 1, "valor": 1}))
    return {d["fecha"]: float(d["valor"]) for d in docs}


def get_cer_en_fecha(cer_dict, fecha_date):
    """Busca el CER de una fecha; retrocede hasta 7 días para feriados/fines de semana."""
    for i in range(7):
        key = (fecha_date - timedelta(days=i)).isoformat()
        if key in cer_dict:
            return cer_dict[key]
    return None


# ─────────────────────────────────────────────
# Cálculo principal por documento
# ─────────────────────────────────────────────

def calcular_campos(doc, instrumento, cer_dict):
    precio = doc.get("price")
    timestamp = doc.get("timestamp")

    if not precio or precio <= 0 or not timestamp:
        return None

    fecha_trade = timestamp.date() if isinstance(timestamp, datetime) else None
    if not fecha_trade:
        return None

    fecha_vto_str = instrumento.get("fecha_vencimiento")
    if not fecha_vto_str:
        return None

    try:
        fecha_vto = date.fromisoformat(fecha_vto_str[:10])
    except Exception:
        return None

    dias_a_vto = (fecha_vto - fecha_trade).days
    if dias_a_vto <= 0:
        return None

    curva = instrumento.get("curva", "")
    flujos_raw = instrumento.get("flujos") or []
    flujo_vto = instrumento.get("flujo_vencimiento")
    resultado = {}

    # ── TASA FIJA (Lecaps / Boncaps) ──────────────────────────────
    if curva == "tasa_fija":
        # Construir flujos futuros
        flujos_futuros = [
            (fecha_flujo(f), monto_flujo(f))
            for f in flujos_raw
            if fecha_flujo(f) and fecha_flujo(f) > fecha_trade and monto_flujo(f) > 0
        ]

        try:
            if flujos_futuros:
                fechas_dt = [datetime.combine(fecha_trade, datetime.min.time())] + \
                            [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
                cf = [-precio] + [m for _, m in flujos_futuros]
                tea = xirr(fechas_dt, cf)
                # Duration con los flujos reales
                if tea is not None:
                    dur = macaulay_duration(
                        [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros],
                        [m for _, m in flujos_futuros],
                        tea,
                        datetime.combine(fecha_trade, datetime.min.time())
                    )
                else:
                    dur = round(dias_a_vto / 365, 4)
            elif flujo_vto and flujo_vto > 0:
                # Zero coupon
                tea = (flujo_vto / precio) ** (365.0 / dias_a_vto) - 1
                dur = round(dias_a_vto / 365, 4)  # Macaulay = maturity para zero coupon
            else:
                return None

            if tea is None or not (-0.5 < tea < 50):
                return None

            tem = (1 + tea) ** (1 / 12) - 1
            resultado["TEA"] = round(tea, 6)
            resultado["TEM"] = round(tem, 6)
            resultado["duration"] = dur

        except Exception:
            return None

    # ── CER ───────────────────────────────────────────────────────
    elif curva == "cer":
        cer_emision = instrumento.get("cer_emision")
        if not cer_emision or cer_emision <= 0:
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado

        cer_trade = get_cer_en_fecha(cer_dict, fecha_trade)
        if not cer_trade:
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado

        ratio = cer_trade / cer_emision

        flujos_futuros = [
            (fecha_flujo(f), monto_flujo(f) * ratio)
            for f in flujos_raw
            if fecha_flujo(f) and fecha_flujo(f) > fecha_trade and monto_flujo(f) > 0
        ]

        try:
            if flujos_futuros:
                fechas_dt = [datetime.combine(fecha_trade, datetime.min.time())] + \
                            [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
                cf = [-precio] + [m for _, m in flujos_futuros]
                tea = xirr(fechas_dt, cf)
                if tea is not None:
                    dur = macaulay_duration(
                        [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros],
                        [m for _, m in flujos_futuros],
                        tea,
                        datetime.combine(fecha_trade, datetime.min.time())
                    )
                else:
                    dur = round(dias_a_vto / 365, 4)
            elif flujo_vto and flujo_vto > 0:
                flujo_ajustado = flujo_vto * ratio
                tea = (flujo_ajustado / precio) ** (365.0 / dias_a_vto) - 1
                dur = round(dias_a_vto / 365, 4)
            else:
                resultado["duration"] = round(dias_a_vto / 365, 4)
                return resultado

            if tea is None or not (-0.5 < tea < 50):
                resultado["duration"] = round(dias_a_vto / 365, 4)
                return resultado

            resultado["TEA"] = round(tea, 6)
            resultado["duration"] = dur

        except Exception:
            resultado["duration"] = round(dias_a_vto / 365, 4)

    # ── TAMAR / DUAL / otros ──────────────────────────────────────
    else:
        resultado["duration"] = round(dias_a_vto / 365, 4)

    return resultado if resultado else None


# ─────────────────────────────────────────────
# Runner principal
# ─────────────────────────────────────────────

def run():
    client = get_mongo_client()
    col_ts = client["Trading"]["TimeSales"]

    print("Cargando Curvas...")
    curvas = cargar_curvas(client)
    print(f"  {len(curvas)} instrumentos en Trading.Curvas")

    print("Cargando CER...")
    cer_dict = cargar_cer(client)
    print(f"  {len(cer_dict)} fechas de CER disponibles")

    tickers = list(curvas.keys())
    total = col_ts.count_documents({"ticker": {"$in": tickers}})
    print(f"  {total} docs de TimeSales a procesar\n")

    cursor = col_ts.find({"ticker": {"$in": tickers}})

    batch = []
    procesados = 0
    salteados = 0

    try:
        for doc in cursor:
            instrumento = curvas[doc["ticker"]]
            campos = calcular_campos(doc, instrumento, cer_dict)

            if campos:
                batch.append(UpdateOne({"_id": doc["_id"]}, {"$set": campos}))
                procesados += 1
            else:
                salteados += 1

            if len(batch) >= BATCH_SIZE:
                col_ts.bulk_write(batch, ordered=False)
                batch = []
                print(f"  {procesados} procesados...", end="\r", flush=True)

        if batch:
            col_ts.bulk_write(batch, ordered=False)

    finally:
        cursor.close()

    print(f"\nListo.")
    print(f"  Actualizados : {procesados}")
    print(f"  Salteados    : {salteados} (vencidos o sin datos suficientes)")


if __name__ == "__main__":
    run()
