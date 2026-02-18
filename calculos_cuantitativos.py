import numpy as np
import yfinance as yf
from scipy.stats import norm


# --- CÁLCULO DE VOLATILIDAD HISTÓRICA ---
def calcular_hv_40_ruedas(ticker="GGAL.BA"):
    """
    Calcula la Volatilidad Histórica institucional de 40 ruedas.
    Fórmula: DESVEST.M(LN_RETORNOS) * RAIZ(260)

    """
    try:
        # Descargamos suficiente historia para 2026
        df = yf.download(ticker, period="1y", progress=False)
        if df.empty: return 0.0

        # Manejo de MultiIndex para GGAL.BA
        prices = df['Close'][ticker] if ('Close', ticker) in df.columns else df['Close']
        prices = prices.dropna()

        # 1. LN Retornos: ln(P_t / P_{t-1})
        ln_returns = np.log(prices / prices.shift(1)).dropna()

        # 2. Desviación Estándar de la muestra (ddof=1 es DESVEST.M)
        # Tomamos exactamente las últimas 40 ruedas
        vol_40 = ln_returns.tail(40).std(ddof=1)

        # 3. Anualización por RAIZ(260)
        # $HV = \sigma_{40} \times \sqrt{260} \times 100$
        hv_final = vol_40 * np.sqrt(260) * 100

        return float(hv_final)
    except Exception:
        return 0.0


# --- MODELO BLACK-SCHOLES ---
def bs_price(S, K, T, r, sigma, option_type='CALL'):
    if T <= 0 or sigma <= 0: return max(0, S - K) if option_type == 'CALL' else max(0, K - S)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if option_type == 'CALL':
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_gamma(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0: return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return norm.pdf(d1) / (S * sigma * np.sqrt(T))


def bs_vega(S, K, T, r, sigma):
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return S * np.sqrt(T) * norm.pdf(d1)


def bs_theta(S, K, T, r, sigma, option_type='CALL'):
    if T <= 0 or sigma <= 0: return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    term1 = -(S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
    term2 = r * K * np.exp(-r * T)
    if option_type == 'CALL':
        res = term1 - term2 * norm.cdf(d2)
    else:
        res = term1 + term2 * norm.cdf(-d2)
    return res / 365


def bs_delta(S, K, T, r, sigma, option_type='CALL'):
    if T <= 0 or sigma <= 0:
        return 1.0 if (option_type == 'CALL' and S > K) else -1.0 if (option_type == 'PUT' and S < K) else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return norm.cdf(d1) if option_type == 'CALL' else norm.cdf(d1) - 1.0


def find_iv(market_price, S, K, T, r, option_type='CALL'):
    intrinsic = max(0, S - K) if option_type == 'CALL' else max(0, K - S)
    if market_price <= (intrinsic + 0.01) or market_price <= 0.05: return 0.0
    sigma = 0.5
    for i in range(20):
        price = bs_price(S, K, T, r, sigma, option_type)
        vega = bs_vega(S, K, T, r, sigma)
        if vega < 0.01: break
        diff = price - market_price
        if abs(diff) < 1e-4: return sigma if sigma < 3.0 else 0.0
        sigma = sigma - diff / vega
    return sigma if (0 < sigma < 3.0) else 0.0


def calc_intrinseco(S, K, tipo='CALL'):
    """Calcula el valor que tendría la opción si venciera hoy."""
    if tipo == 'CALL':
        return max(0, S - K)
    else:
        return max(0, K - S)

def calc_extrinseco(precio_opcion, intrinseco):
    """Calcula el 'valor tiempo' o prima de riesgo."""
    # Si el precio es menor al intrínseco (desarbitraje), el extrínseco es 0
    return max(0, precio_opcion - intrinseco)