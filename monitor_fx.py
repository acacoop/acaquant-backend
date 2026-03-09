import os
import time
import sys
import pyRofex
import threading
from datetime import datetime

from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich import box

from session_manager import inicializar_sesion
from arbitraje_fx.calculadora import calcular_compra_usd, calcular_venta_usd
from arbitraje_fx.mongo_assets import AssetManager
from arbitraje_fx.market_state import MarketState
from arbitraje_fx.trade_logger import TradeLogger

market_state = MarketState()
trade_logger = TradeLogger()
pares_validados = []


def calcular_arbitrajes_cruzados(lista_compra, lista_venta):
    """
    Matching Engine: Cruza dólares baratos con caros consumiendo la liquidez.
    """
    trades = []
    # Copias profundas para ir consumiendo el 'usd' sin romper la tabla visual
    buys = [dict(b) for b in lista_compra if b['tc'] > 0]
    sells = [dict(v) for v in lista_venta if v['tc'] > 0]

    i_b, i_v = 0, 0

    while i_b < len(buys) and i_v < len(sells):
        b = buys[i_b]
        v = sells[i_v]

        # Si la venta no paga más que la compra, se acabó el spread positivo
        if v["tc"] <= b["tc"]:
            break

        # El cuello de botella es el mínimo volumen disponible entre las dos puntas
        match_usd = min(b["usd"], v["usd"])

        if match_usd > 1.0:  # Filtro para ignorar migajas menores a 1 USD
            # Respetamos ESTRICTAMENTE las llaves que espera tu trade_logger
            trades.append({
                "buy_asset": b["asset"],
                "buy_tc": b["tc"],
                "sell_asset": v["asset"],
                "sell_tc": v["tc"],
                "volumen_usd": match_usd,
                "ganancia_ars": match_usd * (v["tc"] - b["tc"]),
                "spread_pct": (v["tc"] / b["tc"] - 1) * 100
            })

            # CONSUMO DE LIQUIDEZ (Fundamental para no duplicar trades)
            b["usd"] -= match_usd
            v["usd"] -= match_usd

        # Movemos los punteros si alguien se quedó sin saldo
        if b["usd"] <= 0.01: i_b += 1
        if v["usd"] <= 0.01: i_v += 1

    return trades


def generar_dashboard():
    lista_compra, lista_venta = [], []
    al30_tc_comp, al30_tc_vend = 0, 0

    # 1. Obtener Market Data y Filtrar con la Calculadora Blindada
    for par in pares_validados:
        p_ars = market_state.obtener_puntas(par["ars"])
        p_usd = market_state.obtener_puntas(par["usd"])

        # Si falta alguna de las 4 puntas, esto ahora devuelve 0 y se ignora (Chau MGCMO falso)
        tc_c, nom_c, usd_c = calcular_compra_usd(p_ars['offer'], p_ars['offer_size'], p_usd['bid'], p_usd['bid_size'],
                                                 par['lote'])
        tc_v, nom_v, usd_v = calcular_venta_usd(p_ars['bid'], p_ars['bid_size'], p_usd['offer'], p_usd['offer_size'],
                                                par['lote'])

        if par["asset"] == "AL30":
            al30_tc_comp, al30_tc_vend = tc_c, tc_v

        if tc_c > 0: lista_compra.append({"asset": par["asset"], "tc": tc_c, "nom": nom_c, "usd": usd_c})
        if tc_v > 0: lista_venta.append({"asset": par["asset"], "tc": tc_v, "nom": nom_v, "usd": usd_v})

    # 2. Ordenamiento: Compra (menor a mayor TC), Venta (mayor a menor TC)
    lista_compra.sort(key=lambda x: x['tc'])
    lista_venta.sort(key=lambda x: x['tc'], reverse=True)

    # 3. Matching Engine y Mongo
    trades = calcular_arbitrajes_cruzados(lista_compra, lista_venta)
    if trades:
        trade_logger.procesar_trades(trades)  # Se manda a Mongo Trading -> ArbitrageHistory

    # --- RENDERIZADO VISUAL ---
    # Tabla Superior: Liquidez
    table = Table(title=f"🎯 HFT ARBITRAJE FX | {datetime.now().strftime('%H:%M:%S')}", box=box.ROUNDED, expand=True)
    table.add_column("COMPRAR USD (TC Min)", style="cyan", header_style="bold cyan")
    table.add_column("VENDER USD (TC Max)", style="magenta", header_style="bold magenta")

    for i in range(8):
        c_s, v_s = "---", "---"
        if i < len(lista_compra):
            c = lista_compra[i]
            c_s = f"{c['asset']:<8} ${c['tc']:>7.2f} | U$S {c['usd']:>7,.0f}"
        if i < len(lista_venta):
            v = lista_venta[i]
            v_s = f"{v['asset']:<8} ${v['tc']:>7.2f} | U$S {v['usd']:>7,.0f}"
        table.add_row(c_s, v_s)

    # Tabla Inferior: Oportunidades Cruzadas
    t_table = Table(title="🚀 MATCHING ENGINE", box=box.SIMPLE, expand=True)
    t_table.add_column("EJECUCIÓN", justify="left")
    t_table.add_column("VOLUMEN USD", justify="right", style="cyan")
    t_table.add_column("PNL EST.", justify="right", style="bold green")
    t_table.add_column("SPREAD", justify="right", style="bold yellow")

    for t in trades[:6]:
        t_table.add_row(
            f"{t['buy_asset']} (${t['buy_tc']:.2f}) ➔ {t['sell_asset']} (${t['sell_tc']:.2f})",
            f"U$S {t['volumen_usd']:,.0f}",
            f"$ {t['ganancia_ars']:,.0f}",
            f"{t['spread_pct']:.2f}%"
        )

    if not trades:
        t_table.add_row("Esperando liquidez o spread positivo...", "", "", "")

    # Layout de la terminal
    layout = Layout()
    layout.split_column(
        Layout(Panel(table, title=f"MEP REF: ${al30_tc_comp:.2f} / ${al30_tc_vend:.2f}", border_style="cyan"), size=15),
        Layout(Panel(t_table, border_style="yellow"))
    )
    return layout


def run():
    global pares_validados
    os.system('cls' if os.name == 'nt' else 'clear')
    print("🚀 Iniciando Motor HFT FX...")

    if not inicializar_sesion(): return

    am = AssetManager()
    resp = pyRofex.get_all_instruments()
    padron = {i['instrumentId']['symbol'] for i in resp['instruments']}

    # Obtenemos los pares filtrando los inactivos
    pares_validados, tickers = am.obtener_pares_activos(padron)

    pyRofex.init_websocket_connection()
    pyRofex.add_websocket_market_data_handler(market_state.actualizar)

    # Suscripción dividida en lotes de 20 para que Rofex no se sature
    print(f"📡 Suscribiendo a {len(tickers)} tickers en bloques...")
    for i in range(0, len(tickers), 20):
        lote_tickers = tickers[i:i + 20]
        pyRofex.market_data_subscription(tickers=lote_tickers,
                                         entries=[pyRofex.MarketDataEntry.BIDS, pyRofex.MarketDataEntry.OFFERS])
        time.sleep(0.1)

    print("✅ Suscripción completa. Levantando Dashboard...")
    time.sleep(1)

    # Live display con refresco a 2 FPS
    with Live(generar_dashboard(), refresh_per_second=2, screen=False) as live:
        try:
            while True:
                live.update(generar_dashboard())
                time.sleep(0.5)
        except KeyboardInterrupt:
            pyRofex.close_websocket_connection()


if __name__ == "__main__":
    run()