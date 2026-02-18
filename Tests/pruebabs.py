import numpy as np
from scipy.stats import norm


def simular_black_scholes(S, K, T_dias, r_anual, precio_mercado, tipo='CALL'):
    # Conversión a años (T)
    T = T_dias / 365
    r = r_anual / 100

    # 1. CALCULO DEL VALOR INTRÍNSECO (PISO TEÓRICO)
    vi = max(0, S - K) if tipo == 'CALL' else max(0, K - S)

    print(f"\n--- ANALISIS DE ESCENARIO ({tipo}) ---")
    print(f"Spot: {S} | Strike: {K} | Días: {T_dias}")
    print(f"Precio en Pantalla: {precio_mercado}")
    print(f"Valor Intrínseco: {vi}")

    # 2. VALIDACIÓN DE ARBITRAJE
    if precio_mercado <= vi:
        print("❌ RESULTADO: DESARBITRAJE.")
        print("La opción vale igual o menos que su valor intrínseco.")
        print("La IV será 0% porque no hay 'Valor Tiempo' (Extrínseco) para calcular.")
        return

    # 3. BUSQUEDA DE IV (Newton-Raphson simplificado)
    sigma = 0.5  # Empieza probando con 50%
    for i in range(100):
        # Protecciones para evitar RuntimeWarnings
        sigma = max(sigma, 0.0001)
        T_safe = max(T, 0.00001)

        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T_safe) / (sigma * np.sqrt(T_safe))
        d2 = d1 - sigma * np.sqrt(T_safe)

        if tipo == 'CALL':
            price = S * norm.cdf(d1) - K * np.exp(-r * T_safe) * norm.cdf(d2)
        else:
            price = K * np.exp(-r * T_safe) * norm.cdf(-d2) - S * norm.cdf(-d1)

        vega = S * np.sqrt(T_safe) * norm.pdf(d1)

        diff = price - precio_mercado
        if abs(diff) < 1e-5: break
        if vega > 0.1:
            sigma = sigma - diff / vega
        else:
            sigma = 0.0001;
            break

    print(f"✅ IV CALCULADA: {round(sigma * 100, 2)}%")
    print(f"Delta: {round(norm.cdf(d1) if tipo == 'CALL' else norm.cdf(d1) - 1, 3)}")


# --- PROBÁ TUS VALORES ACÁ ---

# Caso 1: ATM (Cerca del dinero) - Debería dar IV lógica
simular_black_scholes(S=7555, K=8505, T_dias=17, r_anual=40, precio_mercado=950, tipo='CALL')

# Caso 2: Tu error (Deep ITM sin valor tiempo) - Debería avisar desarbitraje
simular_black_scholes(S=7565, K=8505.4, T_dias=17, r_anual=40, precio_mercado=934, tipo='PUT')
