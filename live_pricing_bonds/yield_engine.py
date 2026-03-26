import pandas as pd
import numpy as np
from scipy.optimize import newton
from datetime import datetime


def calcular_xirr(fechas, flujos):
    dias = np.array([(f - fechas[0]).days for f in fechas])
    anios = dias / 365.0

    def vpn(tir):
        return np.sum(flujos / ((1 + tir) ** anios))

    try:
        return newton(vpn, 0.10)
    except RuntimeError:
        try:
            return newton(vpn, -0.30)
        except RuntimeError:
            return None


def calcular_tir_live(precio_mercado, moneda_cotizacion, data_bono, tc_oficial, tc_mep):
    """
    Motor cross-currency que recibe un precio en vivo y devuelve la TIR.
    """
    if precio_mercado <= 0:
        return None

    naturaleza_flujo = data_bono['moneda_flujo']

    # CROSS-CURRENCY PRICING
    if naturaleza_flujo == "USD":
        # Hard Dollar
        if moneda_cotizacion == "ARS":
            precio_calc = precio_mercado / tc_mep
        else:
            precio_calc = precio_mercado
        multiplicador_flujos = 1.0

    elif naturaleza_flujo == "ARS":
        # Dollar Linked
        if moneda_cotizacion == "USD":
            precio_calc = precio_mercado * tc_mep
        else:
            precio_calc = precio_mercado
        multiplicador_flujos = tc_oficial
    else:
        return None

    # Procesamiento de Fechas y Flujos
    fecha_hoy = datetime.now()
    df = pd.DataFrame(data_bono['flujos'])
    df['fecha'] = pd.to_datetime(df['fecha'])

    df_futuro = df[df['fecha'] > fecha_hoy].copy()
    if df_futuro.empty:
        return None

    flujo_base = df_futuro['amortizacion'] + df_futuro['interes']
    flujos_finales = flujo_base * multiplicador_flujos

    fechas_calc = [fecha_hoy] + df_futuro['fecha'].tolist()
    cf_calc = [-precio_calc] + flujos_finales.tolist()

    tir = calcular_xirr(fechas_calc, cf_calc)
    return tir


def calcular_duration(tir, precio_mercado, moneda_cotizacion, data_bono, tc_oficial, tc_mep):
    """
    Duration de Macaulay en años.
    tir: decimal (ej: 0.08 para 8%). Debe estar pre-calculado con calcular_tir_live.
    """
    if tir is None or precio_mercado <= 0:
        return None

    naturaleza_flujo = data_bono['moneda_flujo']

    if naturaleza_flujo == "USD":
        precio_calc = precio_mercado / tc_mep if moneda_cotizacion == "ARS" else precio_mercado
        multiplicador_flujos = 1.0
    elif naturaleza_flujo == "ARS":
        precio_calc = precio_mercado * tc_mep if moneda_cotizacion == "USD" else precio_mercado
        multiplicador_flujos = tc_oficial
    else:
        return None

    fecha_hoy = datetime.now()
    df = pd.DataFrame(data_bono['flujos'])
    df['fecha'] = pd.to_datetime(df['fecha'])
    df_futuro = df[df['fecha'] > fecha_hoy].copy()

    if df_futuro.empty:
        return None

    flujos_finales = ((df_futuro['amortizacion'] + df_futuro['interes']) * multiplicador_flujos).values
    t = np.array([(f - fecha_hoy).days / 365.0 for f in df_futuro['fecha']])

    pv = flujos_finales / ((1 + tir) ** t)
    duration = np.sum(t * pv) / np.sum(pv)

    return round(duration, 2)