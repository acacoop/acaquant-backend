import numpy as np
from scipy.stats import norm


# --- MODELO BLACK-SCHOLES ---
def bs_price(S, K, T, r, sigma, option_type='CALL'):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return max(0, S - K) if option_type == 'CALL' else max(0, K - S)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if option_type == 'CALL':
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_gamma(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return norm.pdf(d1) / (S * sigma * np.sqrt(T))


def bs_vega(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return S * np.sqrt(T) * norm.pdf(d1)


def bs_theta(S, K, T, r, sigma, option_type='CALL'):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return 0.0
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
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 1.0 if (option_type == 'CALL' and S > K) else -1.0 if (option_type == 'PUT' and S < K) else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return norm.cdf(d1) if option_type == 'CALL' else norm.cdf(d1) - 1.0


def find_iv(market_price, S, K, T, r, option_type='CALL'):
    intrinsic = max(0, S - K) if option_type == 'CALL' else max(0, K - S)
    if market_price <= (intrinsic + 0.01) or market_price <= 0.05: return 0.0
    sigma = 0.5
    for _ in range(20):
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