import os
import time

# Variable Fija
DOLAR_A3500 = 1030.50

# Memoria global simulada
live_board = {
    # --- INSTRUMENTOS SOMBRA (Solo para cálculo de MEP) ---
    "MERV - XMEV - AL30 - 48hs": {
        "asset": "AL30", "emisor": "Gobierno", "vencimiento": "2030", "moneda": "ARS",
        "q_bid": 1500000, "bid": 51000.00, "tir_bid": 0.25,
        "offer": 51100.00, "q_offer": 2000000, "tir_offer": 0.245
    },
    "MERV - XMEV - AL30D - 48hs": {
        "asset": "AL30D", "emisor": "Gobierno", "vencimiento": "2030", "moneda": "USD",
        "q_bid": 80000, "bid": 48.50, "tir_bid": 0.24,
        "offer": 48.60, "q_offer": 100000, "tir_offer": 0.235
    },

    # --- BONOS CORPORATIVOS ---
    "MERV - XMEV - CS48O - 24hs": {
        "asset": "CS48O", "emisor": "Cresud", "vencimiento": "2028", "moneda": "ARS",
        "q_bid": 15000, "bid": 105500.00, "tir_bid": 0.0825,
        "offer": 106000.00, "q_offer": 50000, "tir_offer": 0.0790
    },
    "MERV - XMEV - YMCWO - 24hs": {
        "asset": "YMCWO", "emisor": "YPF", "vencimiento": "2026", "moneda": "ARS",
        "q_bid": 250000, "bid": 144500.00, "tir_bid": -0.0580,
        "offer": 145200.00, "q_offer": 100000, "tir_offer": -0.0635
    },
    "MERV - XMEV - PNFCO - 24hs": {
        "asset": "PNFCO", "emisor": "Pan American", "vencimiento": "2027", "moneda": "ARS",
        "q_bid": 0, "bid": 0.0, "tir_bid": None,
        "offer": 102500.00, "q_offer": 25000, "tir_offer": 0.0650
    }
}


def obtener_mep_dinamico():
    """
    Calcula el Dólar MEP implícito real (Comprar Offer ARS / Vender Bid USD).
    """
    try:
        precio_ars_compra = live_board["MERV - XMEV - AL30 - 48hs"]["offer"]
        precio_usd_venta = live_board["MERV - XMEV - AL30D - 48hs"]["bid"]

        if precio_ars_compra > 0 and precio_usd_venta > 0:
            return precio_ars_compra / precio_usd_venta
    except KeyError:
        pass

    return 1050.00  # Fallback


def limpiar_pantalla():
    os.system('cls' if os.name == 'nt' else 'clear')


def imprimir_pizarra():
    limpiar_pantalla()
    dolar_mep_vivo = obtener_mep_dinamico()

    bonos_visibles = []
    for symbol, data in live_board.items():
        # REGLA 1: Ocultar los tickers de utilidad (AL30 y AL30D)
        if data['asset'] in ['AL30', 'AL30D']:
            continue

        # REGLA 2: Solo mostrar si hay liquidez a la venta (Offer > 0)
        if data.get('offer', 0) > 0:
            bonos_visibles.append(data)

    # REGLA 3: Ordenar de mayor a menor según TIR OFFER
    bonos_visibles.sort(key=lambda x: x.get('tir_offer') or -999, reverse=True)

    print("=" * 130)
    print(f" 🎯 SCREENER YIELD MONITOR | TC OFICIAL: ${DOLAR_A3500} | 🔴 TC MEP EN VIVO: ${dolar_mep_vivo:,.2f}")
    print("=" * 130)
    print(
        f"{'ASSET':<8} | {'EMISOR':<13} | {'VENCE':<5} | {'VOL BID ($)':<13} | {'BID PX':<11} | {'TIR BID':<9} | {'TIR OFFER':<9} | {'OFFER PX':<11} | {'VOL OFF ($)':<13}")
    print("-" * 130)

    for data in bonos_visibles:
        asset = data['asset']
        emisor = data['emisor'][:13]
        vence = data['vencimiento']

        vol_bid = (data.get('q_bid', 0) / 100) * data.get('bid', 0.0)
        vol_offer = (data.get('q_offer', 0) / 100) * data.get('offer', 0.0)

        str_vol_bid = f"${vol_bid:,.0f}" if vol_bid > 0 else "---"
        str_bid = f"${data.get('bid', 0.0):,.2f}" if data.get('bid', 0.0) > 0 else "S/D"
        str_tir_bid = f"{data.get('tir_bid') * 100:.2f}%" if data.get('tir_bid') is not None else "---"

        str_vol_offer = f"${vol_offer:,.0f}" if vol_offer > 0 else "---"
        str_offer = f"${data.get('offer', 0.0):,.2f}" if data.get('offer', 0.0) > 0 else "S/D"
        str_tir_offer = f"{data.get('tir_offer') * 100:.2f}%" if data.get('tir_offer') is not None else "---"

        print(
            f"{asset:<8} | {emisor:<13} | {vence:<5} | {str_vol_bid:<13} | {str_bid:<11} | {str_tir_bid:<9} | {str_tir_offer:<9} | {str_offer:<11} | {str_vol_offer:<13}")

    print("-" * 130)
    print(f"Última actualización: {time.strftime('%H:%M:%S')} (Ordenado por TIR Offer)")


if __name__ == "__main__":
    imprimir_pizarra()