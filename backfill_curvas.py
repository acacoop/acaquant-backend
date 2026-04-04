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
    """Extrae el monto de un flujo tasa_fija (valores absolutos)."""
    if "monto" in f:
        return float(f["monto"])
    return float(f.get("amortizacion", 0)) + float(f.get("interes", 0))


def monto_flujo_cer(f, valor_nominal=100):
    """
    Extrae el monto de un flujo CER (campos en porcentaje sobre VN).
    amortizacion_pct: % del VN que se amortiza.
    cupon_sobre_residual: tasa aplicada sobre residual_previo_pct.
    cupon_anual: tasa anual (solo en zero coupon, siempre 0).
    """
    vn = float(valor_nominal)
    amort = float(f.get("amortizacion_pct", 0)) / 100 * vn
    if "cupon_sobre_residual" in f:
        cupon = float(f.get("cupon_sobre_residual", 0)) * float(f.get("residual_previo_pct", 0)) / 100 * vn
    else:
        cupon = float(f.get("cupon_anual", 0)) * vn
    return amort + cupon


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
    """Devuelve dict { "YYYY-MM-DD": valor }."""
    docs = list(client["Trading"]["CER"].find({}, {"fecha": 1, "valor": 1}))
    return {d["fecha"]: float(d["valor"]) for d in docs}


def cargar_dias_habiles(client):
    """Devuelve lista de fechas hábiles ordenadas ascendente desde Trading.DiasHabiles."""
    docs = list(client["Trading"]["DiasHabiles"].find({}, {"fecha": 1, "_id": 0}))
    return sorted(d["fecha"] for d in docs)


def get_cer_en_fecha(cer_dict, fecha_date):
    """Busca el valor CER para fecha_date, retrocediendo hasta 7 días si no hay dato exacto."""
    for i in range(7):
        key = (fecha_date - timedelta(days=i)).isoformat()
        if key in cer_dict:
            return cer_dict[key]
    return None


def siguiente_dia_habil(dias_habiles, fecha_date):
    """Primer día hábil DESPUÉS de fecha_date según Trading.DiasHabiles."""
    fecha_str = fecha_date.isoformat()
    for f in dias_habiles:
        if f > fecha_str:
            return f
    return None


def get_cer_liquidacion(cer_dict, dias_habiles, fecha_str, n=10):
    """Retrocede n días hábiles desde fecha_str y retorna el valor CER de esa fecha."""
    idx = None
    for i, f in enumerate(dias_habiles):
        if f <= fecha_str:
            idx = i
    if idx is None or idx < n:
        return None
    fecha_n = date.fromisoformat(dias_habiles[idx - n])
    return get_cer_en_fecha(cer_dict, fecha_n)


# ─────────────────────────────────────────────
# Cálculo principal por documento
# ─────────────────────────────────────────────

def calcular_campos(doc, instrumento, cer_dict, dias_habiles):
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

        # Settlement = primer día hábil después del trade
        settlement_str = siguiente_dia_habil(dias_habiles, fecha_trade)
        if not settlement_str:
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado
        fecha_settlement = date.fromisoformat(settlement_str)

        # CER de liquidación = CER en (settlement - 10 días hábiles)
        cer_liq = get_cer_liquidacion(cer_dict, dias_habiles, settlement_str, n=10)
        if not cer_liq:
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado

        ratio = cer_liq / cer_emision
        valor_nominal = float(instrumento.get("valor_nominal", 100))
        precio_tecnico = valor_nominal * ratio
        resultado["paridad"] = round(precio / precio_tecnico * 100, 4)

        dias_a_vto_s = (fecha_vto - fecha_settlement).days
        if dias_a_vto_s <= 0:
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado

        flujos_futuros = [
            (fecha_flujo(f), monto_flujo_cer(f, valor_nominal) * ratio)
            for f in flujos_raw
            if fecha_flujo(f) and fecha_flujo(f) > fecha_settlement and monto_flujo_cer(f, valor_nominal) > 0
        ]

        try:
            if flujos_futuros:
                fechas_dt = [datetime.combine(fecha_settlement, datetime.min.time())] + \
                            [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
                cf = [-precio] + [m for _, m in flujos_futuros]
                tea = xirr(fechas_dt, cf)
                if tea is not None:
                    dur = macaulay_duration(
                        [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros],
                        [m for _, m in flujos_futuros],
                        tea,
                        datetime.combine(fecha_settlement, datetime.min.time())
                    )
                else:
                    dur = round(dias_a_vto_s / 365, 4)
            else:
                resultado["duration"] = round(dias_a_vto_s / 365, 4)
                return resultado

            if tea is None or not (-0.99 < tea < 50):
                resultado["duration"] = round(dias_a_vto_s / 365, 4)
                return resultado

            resultado["TEA"] = round(tea, 6)
            resultado["duration"] = dur

        except Exception:
            resultado["duration"] = round(dias_a_vto_s / 365, 4)

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

    print("Cargando días hábiles...")
    dias_habiles = cargar_dias_habiles(client)
    print(f"  {len(dias_habiles)} días hábiles disponibles")

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
            campos = calcular_campos(doc, instrumento, cer_dict, dias_habiles)

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
