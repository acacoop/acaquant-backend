import numpy as np
from scipy.stats import norm


# --- MODELO BLACK-SCHOLES ---
def _d1_d2(S, K, T, r, sigma):
    """d1/d2 compartidos por precio y todas las griegas — calcularlos UNA vez
    por (S,K,T,r,sigma) en vez de dentro de cada bs_* (el motor de opciones
    los pedía decenas de veces por contrato por tick)."""
    sqrt_T = np.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    return d1, d1 - sigma * sqrt_T


def bs_price(S, K, T, r, sigma, option_type='CALL'):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return max(0, S - K) if option_type == 'CALL' else max(0, K - S)
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if option_type == 'CALL':
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_gamma(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return 0.0
    d1, _ = _d1_d2(S, K, T, r, sigma)
    return norm.pdf(d1) / (S * sigma * np.sqrt(T))


def bs_vega(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return 0.0
    d1, _ = _d1_d2(S, K, T, r, sigma)
    return S * np.sqrt(T) * norm.pdf(d1)


def bs_theta(S, K, T, r, sigma, option_type='CALL'):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return 0.0
    d1, d2 = _d1_d2(S, K, T, r, sigma)
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
    d1, _ = _d1_d2(S, K, T, r, sigma)
    return norm.cdf(d1) if option_type == 'CALL' else norm.cdf(d1) - 1.0


def bs_greeks(S, K, T, r, sigma, option_type='CALL'):
    """Delta/gamma/vega/theta de un saque, con UN solo cálculo de d1/d2.

    Mismos números que llamar bs_delta + bs_gamma + bs_vega + bs_theta por
    separado (que recalculan d1 cada una) — es el camino para el hot loop del
    motor de opciones, que arma las 4 griegas por contrato en cada tick.
    Devuelve dict {delta, gamma, vega, theta} sin redondear."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        delta = (1.0 if (option_type == 'CALL' and S > K)
                 else -1.0 if (option_type == 'PUT' and S < K) else 0.0)
        return {"delta": delta, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    sqrt_T = np.sqrt(T)
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    pdf_d1 = norm.pdf(d1)
    delta = norm.cdf(d1) if option_type == 'CALL' else norm.cdf(d1) - 1.0
    gamma = pdf_d1 / (S * sigma * sqrt_T)
    vega = S * sqrt_T * pdf_d1
    term1 = -(S * pdf_d1 * sigma) / (2 * sqrt_T)
    term2 = r * K * np.exp(-r * T)
    theta = (term1 - term2 * norm.cdf(d2)) if option_type == 'CALL' \
        else (term1 + term2 * norm.cdf(-d2))
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta / 365}


def find_iv(market_price, S, K, T, r, option_type='CALL'):
    intrinsic = max(0, S - K) if option_type == 'CALL' else max(0, K - S)
    if market_price <= (intrinsic + 0.01) or market_price <= 0.05: return 0.0
    sigma = 0.5
    sqrt_T = np.sqrt(T)
    disc_K = K * np.exp(-r * T)
    for _ in range(20):
        # Newton-Raphson: precio y vega comparten d1/d2 → un solo cálculo por
        # iteración (antes bs_price + bs_vega lo hacían dos veces cada una).
        d1, d2 = _d1_d2(S, K, T, r, sigma)
        if option_type == 'CALL':
            price = S * norm.cdf(d1) - disc_K * norm.cdf(d2)
        else:
            price = disc_K * norm.cdf(-d2) - S * norm.cdf(-d1)
        vega = S * sqrt_T * norm.pdf(d1)
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