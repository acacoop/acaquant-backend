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
from engines._curvas_loader import cargar_indexado_por_ticker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorCurvas")

INTERVALO_SEGUNDOS = 5
INTERVALO_RECARGA_CER = 3600   # recarga CER cada 1 hora
INTERVALO_RECARGA_MEP = 60     # refresca MEP cada 1 min (para soberanos)
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


def convexity(fechas_flujos, montos, tir, fecha_base):
    """Convexity (segunda derivada del precio respecto al yield, normalizada).

    Fórmula: C = (1/P) × Σ [t(t+1) × CF_t / (1+y)^(t+2)]

    Dimensiones: años². Usar en aproximación de Taylor de 2º orden:
        ΔP/P ≈ -D·Δy + (1/2)·C·Δy²

    El término convex es siempre positivo cuando los flujos son positivos,
    de ahí la propiedad financiera "la convexity te ayuda" (cuando cae la
    tasa el precio sube más de lo que duration estimaría; cuando sube,
    cae menos). Útil para escenarios de stress > 100 bps.

    Devuelve None si total_pv ≤ 0 (igual fallback que macaulay_duration).
    """
    t = np.array([(f - fecha_base).days / 365.0 for f in fechas_flujos])
    cf = np.array(montos, dtype=float)
    pv = cf / ((1 + tir) ** t)
    total_pv = np.sum(pv)
    if total_pv <= 0:
        return None
    sum_term = np.sum(t * (t + 1) * cf / ((1 + tir) ** (t + 2)))
    return round(float(sum_term / total_pv), 4)


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


def monto_flujo_soberano(f, valor_nominal=100):
    """Flujo USD de un bono soberano hard-dollar.

    Atención con la semántica del campo: aunque `cupon_sobre_residual` suena
    a "tasa × residual", en los JSONs de soberanos del prospecto el valor
    ya viene MULTIPLICADO — es el monto del cupón en USD por cada 100 de VN.
    Ej. GD30 2026-07-13 con residual 72% y tasa anual 0.75%:
        tasa_semestral · residual · VN = 0.00375 · 0.72 · 100 = 0.27 USD
        → en el JSON aparece `cupon_sobre_residual: 0.27` (ya resuelto).

    Por eso el cálculo NO multiplica de nuevo por residual_previo_pct.
    La amortización sigue siendo % del VN original.

    amortizacion_pct · VN  +  cupon_sobre_residual · VN / 100
    """
    vn = float(valor_nominal)
    amort = float(f.get("amortizacion_pct", 0)) / 100 * vn
    cupon = float(f.get("cupon_sobre_residual", 0)) / 100 * vn
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

def cargar_cer(client, dias: int = 90):
    """Carga CER solo de los últimos N días.

    El enriquecimiento de trades usa T-10 días hábiles como settlement,
    así que los valores más viejos nunca se consultan en tiempo real.
    Carga completa crece monotónica sin sentido.
    """
    desde = (date.today() - timedelta(days=dias)).isoformat()
    docs = list(client["Trading"]["CER"].find(
        {"fecha": {"$gte": desde}}, {"fecha": 1, "valor": 1, "_id": 0},
    ))
    logger.info(f"CER cargado: {len(docs)} fechas (últimos {dias} días)")
    return {d["fecha"]: float(d["valor"]) for d in docs}


def cargar_dias_habiles(client):
    docs = list(client["Trading"]["DiasHabiles"].find({}, {"fecha": 1, "_id": 0}))
    dias = sorted(d["fecha"] for d in docs)
    logger.info(f"Días hábiles cargados: {len(dias)}")
    return dias


def cargar_mep_actual(client) -> float | None:
    """Último MEP disponible. Prefiere DolarSnapshot live; cae al histórico.

    Usado para convertir precios de bonos soberanos ley-NY en pesos
    (ticker sin sufijo D/C) a USD antes del cálculo de YTM.
    """
    snap = client["Valuaciones"]["DolarSnapshot"].find_one(
        {"_id": "current"}, {"_id": 0, "mep": 1},
    )
    if snap and snap.get("mep"):
        return float(snap["mep"])
    doc = client["Valuaciones"]["Dolar"].find_one(
        {"mep": {"$gt": 0}}, {"_id": 0, "mep": 1}, sort=[("timestamp", -1)],
    )
    if doc and doc.get("mep"):
        return float(doc["mep"])
    return None


def precio_soberano_a_usd(precio: float, ticker_completo: str, mep: float | None) -> float | None:
    """Convierte precio de bono soberano a USD según el sufijo del ticker ROFEX.

    El detector mira el tercer segmento del ticker completo
    ('MERV - XMEV - <SIMBOLO> - 24hs'), porque el `ticker_corto` es un
    label humano y puede no reflejar la moneda (ej. usuario deja
    ticker_corto='GD30' aunque el ticker sea 'GD30D' en USD).

    - …/GD30D/…, …/GD30C/…  → precio ya en USD (retorna tal cual)
    - …/GD30/…               → precio en pesos, divide por MEP
    - Si falta MEP cuando se necesita → None (no enriquece).
    """
    if precio is None or precio <= 0 or not ticker_completo:
        return None
    partes = ticker_completo.split(" - ")
    simbolo = partes[2] if len(partes) >= 3 else ticker_completo
    sufijo = simbolo[-1].upper() if simbolo else ""
    if sufijo in ("D", "C"):
        return precio
    if not mep or mep <= 0:
        return None
    return precio / mep


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

def calcular_campos(doc, instrumento, cer_dict, dias_habiles, mep: float | None = None):
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
            conv = None
            if flujos_futuros:
                fechas_dt = [datetime.combine(fecha_trade, datetime.min.time())] + \
                            [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
                cf = [-precio] + [m for _, m in flujos_futuros]
                tea = xirr(fechas_dt, cf)
                if tea is not None:
                    fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
                    montos_flujos    = [m for _, m in flujos_futuros]
                    fecha_base_dt    = datetime.combine(fecha_trade, datetime.min.time())
                    dur  = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                    conv = convexity(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                else:
                    dur = round(dias_a_vto / 365, 4)
            elif flujo_vto and flujo_vto > 0:
                tea = (flujo_vto / precio) ** (365.0 / dias_a_vto) - 1
                dur = round(dias_a_vto / 365, 4)
                # Zero coupon: un único flujo al vto. C = t(t+1)/(1+y)^2.
                fecha_base_dt = datetime.combine(fecha_trade, datetime.min.time())
                fecha_vto_dt  = datetime.combine(fecha_vto,   datetime.min.time())
                conv = convexity([fecha_vto_dt], [flujo_vto], tea, fecha_base_dt)
            else:
                return None

            if tea is None or not (-0.5 < tea < 50):
                return None

            tem = (1 + tea) ** (1 / 12) - 1
            resultado["TEA"] = round(tea, 6)
            resultado["TEM"] = round(tem, 6)
            resultado["duration"] = dur
            if conv is not None:
                resultado["convexity"] = conv

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
            conv = None
            if flujos_futuros:
                fechas_dt = [datetime.combine(fecha_settlement, datetime.min.time())] + \
                            [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
                cf = [-precio] + [m for _, m in flujos_futuros]
                tea = xirr(fechas_dt, cf)
                if tea is not None:
                    fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
                    montos_flujos    = [m for _, m in flujos_futuros]
                    fecha_base_dt    = datetime.combine(fecha_settlement, datetime.min.time())
                    dur  = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                    conv = convexity(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
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
            if conv is not None:
                resultado["convexity"] = conv

        except Exception:
            resultado["duration"] = round(dias_a_vto_s / 365, 4)

    # ── SOBERANOS (Globales/Bonares hard-dollar) ──────────────────
    elif curva == "soberanos":
        # Settlement T+1 (primer día hábil después del trade).
        settlement_str = siguiente_dia_habil(dias_habiles, fecha_trade)
        if settlement_str:
            fecha_settlement = date.fromisoformat(settlement_str)
        else:
            fecha_settlement = fecha_trade

        valor_nominal = float(instrumento.get("valor_nominal", 100))
        ticker_completo = instrumento.get("ticker") or ""

        # Precio a USD — divide por MEP solo si el ticker cotiza en pesos.
        # Decide según el sufijo del símbolo ROFEX (no del ticker_corto, que
        # es un label humano y puede no coincidir con la moneda real).
        precio_usd = precio_soberano_a_usd(precio, ticker_completo, mep)
        if precio_usd is None:
            # Sin MEP no podemos calcular YTM USD; dejamos duration ingenua.
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado

        # Filtrar solo flujos futuros al settlement.
        flujos_futuros = [
            (fecha_flujo(f), monto_flujo_soberano(f, valor_nominal), f)
            for f in flujos_raw
            if fecha_flujo(f)
            and fecha_flujo(f) > fecha_settlement
            and monto_flujo_soberano(f, valor_nominal) > 0
        ]

        if not flujos_futuros:
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado

        # Paridad = precio_usd / (residual_previo del primer flujo vivo).
        # Es el % de valor técnico pagado respecto al nominal vivo.
        primer_flujo = flujos_futuros[0][2]
        residual_vivo = float(primer_flujo.get("residual_previo_pct", 100))
        if residual_vivo > 0:
            resultado["paridad"] = round(precio_usd / residual_vivo * 100, 4)

        try:
            fechas_dt = [datetime.combine(fecha_settlement, datetime.min.time())] + \
                        [datetime.combine(fd, datetime.min.time()) for fd, _, _ in flujos_futuros]
            cf = [-precio_usd] + [m for _, m, _ in flujos_futuros]
            tea = xirr(fechas_dt, cf)

            if tea is None or not (-0.5 < tea < 10):
                # YTM no convergió o está fuera de rango razonable USD (50%-1000%
                # para soberanos stressed, pero >1000% es error numérico).
                resultado["duration"] = round(dias_a_vto / 365, 4)
                return resultado

            fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _, _ in flujos_futuros]
            montos_flujos    = [m for _, m, _ in flujos_futuros]
            fecha_base_dt    = datetime.combine(fecha_settlement, datetime.min.time())

            dur  = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
            conv = convexity(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)

            resultado["TEA"] = round(tea, 6)
            resultado["duration"] = dur if dur is not None else round(dias_a_vto / 365, 4)
            if conv is not None:
                resultado["convexity"] = conv

        except Exception:
            resultado["duration"] = round(dias_a_vto / 365, 4)

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

    curvas = cargar_indexado_por_ticker()
    logger.info(f"Curvas cargadas: {len(curvas)} instrumentos")
    cer_dict = cargar_cer(client)
    dias_habiles = cargar_dias_habiles(client)
    mep_actual = cargar_mep_actual(client)
    tickers = list(curvas.keys())

    ultimo_reload_cer = time.time()
    ultimo_reload_mep = time.time()

    logger.info(
        "Escuchando %d tickers. Loop cada %ds. MEP inicial: %s",
        len(tickers), INTERVALO_SEGUNDOS, mep_actual,
    )

    while True:
        try:
            # Recargar CER cada hora (el dato se actualiza diariamente)
            if time.time() - ultimo_reload_cer > INTERVALO_RECARGA_CER:
                cer_dict = cargar_cer(client)
                ultimo_reload_cer = time.time()

            # Recargar MEP cada minuto (motor_dolares lo actualiza cada 5s).
            if time.time() - ultimo_reload_mep > INTERVALO_RECARGA_MEP:
                mep_actual = cargar_mep_actual(client)
                ultimo_reload_mep = time.time()

            # Buscar docs sin enriquecer — más recientes primero para no bloquear trades nuevos
            docs = list(col_ts.find(
                {"ticker": {"$in": tickers}, "duration": {"$exists": False}},
                sort=[("timestamp", -1)],
                limit=BATCH_SIZE
            ))

            if docs:
                ops = []
                # Para cada ticker, retenemos el enriquecimiento del trade
                # más reciente para propagarlo al MarketSnapshot después.
                por_ticker_reciente: dict[str, tuple[datetime, dict]] = {}

                for doc in docs:
                    instrumento = curvas.get(doc["ticker"])
                    if not instrumento:
                        continue
                    campos = calcular_campos(doc, instrumento, cer_dict, dias_habiles, mep_actual)
                    if not campos:
                        # Marcamos el doc con duration: null para que el filtro
                        # {duration: {$exists: false}} deje de devolverlo. Sin
                        # esto el motor entra en loop infinito sobre docs que
                        # no se pueden enriquecer (ej. ticker sin TEA derivable)
                        # y nunca llega a procesar los más viejos.
                        ops.append(UpdateOne(
                            {"_id": doc["_id"]}, {"$set": {"duration": None}},
                        ))
                        continue
                    ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": campos}))

                    # Guardar el más reciente por ticker (los docs vienen
                    # ordenados desc por timestamp, pero dentro del batch
                    # puede repetirse ticker).
                    ts = doc.get("timestamp")
                    if ts:
                        prev = por_ticker_reciente.get(doc["ticker"])
                        if prev is None or ts > prev[0]:
                            por_ticker_reciente[doc["ticker"]] = (ts, campos)

                if ops:
                    col_ts.bulk_write(ops, ordered=False)
                    logger.info(f"{len(ops)} docs enriquecidos.")

                # Propagar los campos analíticos al MarketSnapshot del
                # ticker — así la tabla de renta fija y cualquier otro
                # consumer del snapshot ven TEA/duration/paridad sin tener
                # que hacer un join extra a TimeSales.
                if por_ticker_reciente:
                    col_ms = client["Trading"]["MarketSnapshot"]
                    ops_ms = []
                    for ticker, (_, campos) in por_ticker_reciente.items():
                        updates = {}
                        for field in ("TEA", "TEM", "duration", "convexity", "paridad"):
                            if field in campos:
                                updates[f"metrics.{field}"] = campos[field]
                        if updates:
                            ops_ms.append(UpdateOne(
                                {"ticker": ticker}, {"$set": updates},
                            ))
                    if ops_ms:
                        col_ms.bulk_write(ops_ms, ordered=False)

        except Exception:
            logger.error(f"Error en loop:\n{traceback.format_exc()}")

        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    run()
