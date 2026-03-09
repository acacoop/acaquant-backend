import pyRofex
import time
import os
import sys
import threading
from datetime import datetime
from pymongo import MongoClient

from session_manager import inicializar_sesion
from arbitraje_fx.market_state import MarketState
from arbitraje_fx.calculadora_tasas import calcular_trade_a
from arbitraje_fx.buscador_caucion import obtener_caucion_mas_corta

# Memoria compartida
market_state = MarketState()
pares_tasas = []
ticker_caucion = ""
dias_caucion = 0


class TasasManager:
    def __init__(self, mongo_uri="mongodb://localhost:27017/"):
        self.client = MongoClient(mongo_uri)
        self.coleccion = self.client["Trading"]["TasasAssets"]

    def obtener_pares_activos(self, padron_rofex):
        documentos = self.coleccion.find({"activo": True})
        pares_validos = []
        tickers_a_suscribir = set()

        for doc in documentos:
            pata_ci = doc["patas"]["ci"]
            pata_24 = doc["patas"]["24hs"]
            # Validamos que existan hoy en el mercado
            if pata_ci in padron_rofex and pata_24 in padron_rofex:
                pares_validos.append({
                    "asset": doc["asset"],
                    "tipo": doc["tipo"],
                    "lote": doc["lote"],
                    "ci": pata_ci,
                    "24hs": pata_24
                })
                tickers_a_suscribir.update([pata_ci, pata_24])
        return pares_validos, list(tickers_a_suscribir)


def dibujar_dashboard_tasas():
    if os.name == 'nt': os.system("")
    print('\033[2J', end='')

    while True:
        puntas_caucion = market_state.obtener_puntas(ticker_caucion)
        tna_tomadora = puntas_caucion['offer']

        lista_trades = []
        for par in pares_tasas:
            p_ci = market_state.obtener_puntas(par["ci"])
            p_24 = market_state.obtener_puntas(par["24hs"])

            # Solo calculamos si hay puntas reales (no vacías)
            if p_ci['offer'] > 0 and p_24['bid'] > 0:
                tna_imp, spread, max_nom, cap_req = calcular_trade_a(
                    offer_ci=p_ci['offer'], size_offer_ci=p_ci['offer_size'],
                    bid_24hs=p_24['bid'], size_bid_24hs=p_24['bid_size'],
                    tna_caucion_tomadora=tna_tomadora, dias_plazo=dias_caucion, lote=par['lote']
                )
                lista_trades.append({"asset": par["asset"], "tna_imp": tna_imp, "spread": spread, "nominales": max_nom,
                                     "capital": cap_req})

        lista_trades.sort(key=lambda x: x['spread'], reverse=True)

        buffer = ['\033[H']
        buffer.append("=" * 110)
        buffer.append(f" 📈 HFT MONITOR DE TASAS | {len(pares_tasas)} ACTIVOS | 🕒 {datetime.now().strftime('%H:%M:%S')}")
        buffer.append("-" * 110)
        c_str = f"{tna_tomadora:.2f}%" if tna_tomadora > 0 else "SIN DATA"
        buffer.append(f" 🏦 CAUCIÓN: {ticker_caucion} | TASA TOMADORA: {c_str}")
        buffer.append("=" * 110)
        buffer.append(
            f" {'ASSET':<10} | {'TNA IMP.':<12} | {'SPREAD NETO':<12} | {'MAX NOM.':<10} | {'CAPITAL ARS':<18}")
        buffer.append("-" * 110)

        for t in lista_trades[:15]:
            color = "\033[92m" if t['spread'] > 0 else ""
            reset = "\033[0m"
            buffer.append(
                f" {t['asset']:<10} | {t['tna_imp']:>10.2f}% | {color}{t['spread']:>11.2f}%{reset} | {t['nominales']:<10,} | $ {t['capital']:<16,.0f}")

        sys.stdout.write('\n'.join(buffer) + '\033[J')
        sys.stdout.flush()
        time.sleep(0.1)


def run():
    global pares_tasas, ticker_caucion, dias_caucion

    if not inicializar_sesion(): return

    res = pyRofex.get_all_instruments()
    padron = {item['instrumentId']['symbol'] for item in res['instruments']}
    ticker_caucion, dias_caucion = obtener_caucion_mas_corta()

    mgr = TasasManager()
    pares_tasas, tickers = mgr.obtener_pares_activos(padron)
    if ticker_caucion: tickers.append(ticker_caucion)

    # WebSocket
    pyRofex.init_websocket_connection()
    pyRofex.add_websocket_market_data_handler(market_state.actualizar)

    print("⏳ Esperando estabilidad del socket (5s)...")
    time.sleep(3)

    # SUSCRIPCIÓN EN LOTES (Batching de 50)
    batch_size = 100
    print(f"📡 Suscribiendo {len(tickers)} instrumentos en lotes de {batch_size}...")
    for i in range(0, len(tickers), batch_size):
        lote = tickers[i:i + batch_size]
        pyRofex.market_data_subscription(tickers=lote,
                                         entries=[pyRofex.MarketDataEntry.BIDS, pyRofex.MarketDataEntry.OFFERS])
        print(f"✅ Lote {i // batch_size + 1} enviado.")
        time.sleep(0.5)

    threading.Thread(target=dibujar_dashboard_tasas, daemon=True).start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        pyRofex.close_websocket_connection()


if __name__ == "__main__":
    run()