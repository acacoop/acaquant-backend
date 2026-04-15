"""
main_curvas.py — Motor de enriquecimiento en tiempo real para Trading.TimeSales.

Corre como servicio paralelo a main_valores.py. Cada 5 segundos busca docs
nuevos en TimeSales (tickers de Trading.Curvas sin campo 'duration') y les
agrega TEA, TEM, Duration y Paridad según el tipo de instrumento.

No toca main_valores.py ni ningún otro motor.

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/main_curvas.py
"""

import logging
import time
import traceback
from datetime import date, datetime, timedelta

import numpy as np
from pymongo import UpdateOne
from scipy.optimize import newton

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorCurvas")

INTERVALO_SEGUNDOS = 5
INTERVALO_RECARGA_CER = 3600   # recarga CER cada 1 hora
BATCH_SIZE = 200


# ─────────────────────────────────────────────
# Helpers de cálculo (idénticos a backfill_curvas)
# ─────────────────────────────────────────────

def xirr(fechas, flujos):
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
    t = np.array([(f - fecha_base).days / 365.0 for f in fechas_flujos])
    cf = np.array(montos, dtype=float)
    pv = cf / ((1 + tir) ** t)
    total_pv = np.sum(pv)
    if total_pv <= 0:
        return None
    return round(float(np.sum(t * pv) / total_pv), 4)


def monto_flujo(f):
    if "monto" in f:
        return float(f["monto"])
    return float(f.get("amortizacion", 0)) + float(f.get("interes", 0))


def monto_flujo_cer(f, valor_nominal=100):
    vn = float(valor_nominal)
    amort = float(f.get("amortizacion_pct", 0)) / 100 * vn
    if "cupon_sobre_residual" in f:
        cupon = float(f.get("cupon_sobre_residual", 0)) * float(f.get("residual_previo_pct", 0)) / 100 * vn
    else:
        cupon = float(f.get("cupon_anual", 0)) * vn
    return amort + cupon


def fecha_flujo(f):
    v = f.get("fecha")
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        return date.fromisoformat(v[:10])
    return None


def get_cer_en_fecha(cer_dict, fecha_date):
    for i in range(7):
        key = (fecha_date - timedelta(days=i)).isoformat()
        if key in cer_dict:
            return cer_dict[key]
    return None


# ─────────────────────────────────────────────
# Carga de datos de referencia
# ─────────────────────────────────────────────

def cargar_curvas(client):
    docs = list(client["Trading"]["Curvas"].find({}))
    logger.info(f"Curvas cargadas: {len(docs)} instrumentos")
    return {d["ticker"]: d for d in docs}


def cargar_cer(client):
    docs = list(client["Trading"]["CER"].find({}, {"fecha": 1, "valor": 1}))
    logger.info(f"CER cargado: {len(docs)} fechas")
    cer_dict = {d["fecha"]: float(d["valor"]) for d in docs}
    return cer_dict


def cargar_dias_habiles(client):
    docs = list(client["Trading"]["DiasHabiles"].find({}, {"fecha": 1, "_id": 0}))
    dias = sorted(d["fecha"] for d in docs)
    logger.info(f"Días hábiles cargados: {len(dias)}")
    return dias


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

    # ── TASA FIJA ─────────────────────────────────────────────────
    if curva == "tasa_fija":
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
                tea = (flujo_vto / precio) ** (365.0 / dias_a_vto) - 1
                dur = round(dias_a_vto / 365, 4)
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
# Loop principal
# ─────────────────────────────────────────────

def run():
    logger.info("Motor Curvas iniciando...")
    client = get_mongo_client()
    col_ts = client["Trading"]["TimeSales"]

    curvas = cargar_curvas(client)
    cer_dict = cargar_cer(client)
    dias_habiles = cargar_dias_habiles(client)
    tickers = list(curvas.keys())

    ultimo_reload_cer = time.time()

    logger.info(f"Escuchando {len(tickers)} tickers. Loop cada {INTERVALO_SEGUNDOS}s.")

    while True:
        try:
            # Recargar CER cada hora (el dato se actualiza diariamente)
            if time.time() - ultimo_reload_cer > INTERVALO_RECARGA_CER:
                cer_dict = cargar_cer(client)
                ultimo_reload_cer = time.time()

            # Buscar docs sin enriquecer — más recientes primero para no bloquear trades nuevos
            docs = list(col_ts.find(
                {"ticker": {"$in": tickers}, "duration": {"$exists": False}},
                sort=[("timestamp", -1)],
                limit=BATCH_SIZE
            ))

            if docs:
                ops = []
                for doc in docs:
                    instrumento = curvas.get(doc["ticker"])
                    if not instrumento:
                        continue
                    campos = calcular_campos(doc, instrumento, cer_dict, dias_habiles)
                    if campos:
                        ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": campos}))

                if ops:
                    col_ts.bulk_write(ops, ordered=False)
                    logger.info(f"{len(ops)} docs enriquecidos.")

        except Exception:
            logger.error(f"Error en loop:\n{traceback.format_exc()}")

        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    run()
