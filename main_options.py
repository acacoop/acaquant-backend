import os
import threading
import time
import pyRofex
from datetime import datetime
from collections import defaultdict

# --- IMPORTAMOS TU DATA Y MANAGERS ---
from Opciones.estrategias_opciones import ESTRATEGIAS
from Opciones.calculos_cuantitativos import (
    calc_intrinseco, find_iv, bs_delta, bs_gamma, bs_vega, bs_theta
)
from mongo_manager import MongoManager
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager

# --- RICH Y TEXTUAL ---
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.console import Group
from rich import box
from textual.app import App, ComposeResult
from textual.widgets import Static
from textual.containers import ScrollableContainer


# ==========================================
# 1. EL CEREBRO: OptionsEngine
# ==========================================
class OptionsEngine:
    def __init__(self):
        self.spot_symbol = "MERV - XMEV - GGAL - 24hs"
        self.tasa = 0.242

        # Conexiones a bases
        self.mongo = MongoManager(db_name="Opciones", collection_name="Data")

        # Mapeos y Estados
        self.mapa_opciones, self.agrupacion_strikes = self._generar_maestra()
        self.market_state = {}
        self.last_trade_cache = {}

        self._inicializar_estado_memoria()

    def _generar_maestra(self):
        """Descarga el padrón y filtra opciones de GGAL para Abril."""
        res = pyRofex.get_detailed_instruments()
        mapa, agrupacion = {}, defaultdict(dict)
        if not res or res.get('status') != 'OK': return mapa, agrupacion

        for inst in res['instruments']:
            if inst.get('underlying') == "Grupo Financiero Galicia Merval":
                cfi = inst.get('cficode', '')
                vence_raw = inst.get('maturity_date', inst.get('maturityDate', ''))

                # Filtro para Abril (04)
                if cfi.startswith('O') and len(vence_raw) == 8 and vence_raw[4:6] == "04":
                    sym = inst['instrumentId']['symbol']
                    strike = float(inst.get('strike', 0))
                    tipo = 'CALL' if cfi == 'OCASPS' else 'PUT' if cfi == 'OPASPS' else None
                    if tipo and strike:
                        mapa[sym] = {'strike': strike, 'tipo': tipo, 'vence': vence_raw}
                        agrupacion[strike][tipo] = sym
        return mapa, dict(agrupacion)

    def _inicializar_estado_memoria(self):
        """Prepara el diccionario para el Spot, Opciones y Estrategias."""
        tickers_a_monitorear = list(self.mapa_opciones.keys()) + [self.spot_symbol]
        for t in set(tickers_a_monitorear):
            self.market_state[t] = {
                'bid': 0, 'offer': 0, 'last': 0, 'last_timestamp': None,
                'open': 0, 'high': 0, 'low': 0, 'ev': 0
            }

    def get_tickers_suscripcion(self):
        return list(self.market_state.keys())

    def update_price(self, ticker, data):
        """Maneja la entrada del WebSocket y actualiza la RAM."""
        if ticker not in self.market_state: return
        state = self.market_state[ticker]

        # 1. Puntas (BID/OFFER)
        if 'BI' in data and data['BI']: state['bid'] = data['BI'][0]['price']
        if 'OF' in data and data['OF']: state['offer'] = data['OF'][0]['price']

        # 2. Datos de Mercado (OP, HI, LO, EV) con blindaje de nombres
        state['open'] = data.get('OP', data.get('OPENING_PRICE', state['open']))
        state['high'] = data.get('HI', data.get('HIGH_PRICE', state['high']))
        state['low'] = data.get('LO', data.get('LOW_PRICE', state['low']))
        state['ev'] = data.get('EV', data.get('TRADE_EFFECTIVE_VOLUME', state['ev']))

        # 3. Último operado (LAST)
        la = data.get('LA')
        if la and la.get('price', 0) > 0:
            state['last'] = la['price']
            ts = datetime.fromtimestamp(la['date'] / 1000.0)
            state['last_timestamp'] = ts

            if ticker in self.mapa_opciones:
                if self.last_trade_cache.get(ticker) != ts:
                    self.last_trade_cache[ticker] = ts
                    threading.Thread(target=self._guardar_en_mongo, args=(ticker, state.copy(), ts),
                                     daemon=True).start()

    def _guardar_en_mongo(self, ticker, state_copy, ts):
        """Calcula Griegas y persiste en segundo plano."""
        S = self.market_state[self.spot_symbol]['last']
        if S <= 0: return

        info = self.mapa_opciones[ticker]
        K = info['strike']
        T = max((datetime.strptime(info['vence'], "%Y%m%d") - datetime.now()).days, 1) / 365.0

        vi = calc_intrinseco(S, K, info['tipo'])
        p_iv = state_copy['last'] if state_copy['last'] > vi else vi + 0.1

        griegas = None
        try:
            iv = find_iv(p_iv, S, K, T, self.tasa, info['tipo'])
            if iv > 0:
                griegas = {
                    "iv": round(iv, 4),
                    "delta": round(bs_delta(S, K, T, self.tasa, iv, info['tipo']), 3),
                    "gamma": round(bs_gamma(S, K, T, self.tasa, iv), 4),
                    "vega": round(bs_vega(S, K, T, self.tasa, iv), 2),
                    "theta": round(bs_theta(S, K, T, self.tasa, iv, info['tipo']), 2)
                }
        except:
            pass

        state_copy['strike'] = K
        state_copy['tipo'] = info['tipo']
        state_copy['spot'] = S
        self.mongo.guardar_operacion_unica(ticker, state_copy, server_time=datetime.now(), griegas=griegas)

    def get_view_data(self):
        """Sincroniza el estado de la RAM con lo que requiere la UI para dibujar."""
        spot_st = self.market_state.get(self.spot_symbol, {})
        S = spot_st.get('last', 0) if spot_st.get('last', 0) > 0 else (spot_st.get('bid', 0) + spot_st.get('offer',
                                                                                                           0)) / 2

        calc_map = {}
        sym_to_strike = {}

        # Sincronizamos todos los campos de market_state al calc_map
        for sym, md in self.market_state.items():
            if sym == self.spot_symbol: continue
            p_mid = (md['bid'] + md['offer']) / 2 if md['bid'] > 0 and md['offer'] > 0 else md['last']

            calc_map[sym] = {
                'bid': md['bid'],
                'offer': md['offer'],
                'last': md['last'],
                'mid': p_mid,
                'open': md['open'],
                'high': md['high'],
                'low': md['low'],
                'ev': md['ev'],
                'iv': 0, 'delta': 0, 'gamma': 0, 'theta': 0
            }

        if S > 0:
            for strike, ops in self.agrupacion_strikes.items():
                for tipo in ['CALL', 'PUT']:
                    sym = ops.get(tipo)
                    if not sym or sym not in calc_map: continue
                    sym_to_strike[sym] = strike
                    p_ref = calc_map[sym]['mid']

                    if p_ref > 0:
                        T = 38 / 365.0  # Aproximación para Abril
                        vi = calc_intrinseco(S, strike, tipo)
                        p_iv = p_ref if p_ref > vi else vi + 0.1
                        try:
                            iv = find_iv(p_iv, S, strike, T, self.tasa, tipo)
                            if iv > 0:
                                calc_map[sym]['iv'] = iv
                                calc_map[sym]['delta'] = bs_delta(S, strike, T, self.tasa, iv, tipo)
                                calc_map[sym]['gamma'] = bs_gamma(S, strike, T, self.tasa, iv)
                                calc_map[sym]['theta'] = bs_theta(S, strike, T, self.tasa, iv, tipo)
                        except:
                            pass
        return S, calc_map, sym_to_strike


# ==========================================
# 2. LA INTERFAZ: Renderizado y App Textual
# ==========================================
def renderizar_tablas(S, calc_map, sym_to_strike, agrupacion):
    def fmt_vol(v):
        # Forzamos que v sea un número, si es None ponemos 0
        val = v if v is not None else 0
        if val == 0: return "-"
        if val >= 1_000_000: return f"{val / 1_000_000:.1f}M"
        if val >= 1_000: return f"{val / 1_000:.0f}k"
        return f"{val:.0f}"

    def get_safe_val(d, key):
        """Extrae el valor del diccionario asegurando que no sea None."""
        if not d: return 0
        val = d.get(key, 0)
        return val if val is not None else 0

    t_cruz = Table(box=box.SIMPLE_HEAVY, expand=False)
    t_cruz.padding = (0, 1)

    # Definición de columnas
    t_cruz.add_column("VOL $", justify="right", style="cyan")
    t_cruz.add_column("H/L", justify="center", style="dim white")
    t_cruz.add_column("DELTA", justify="right", style="green")
    t_cruz.add_column("IV", justify="right", style="white")
    t_cruz.add_column("BID", justify="right", style="bold green")
    t_cruz.add_column("OFFER", justify="right", style="bold red")
    t_cruz.add_column("STRIKE", justify="center", style="bold yellow")
    t_cruz.add_column("BID", justify="left", style="bold green")
    t_cruz.add_column("OFFER", justify="left", style="bold red")
    t_cruz.add_column("IV", justify="left", style="white")
    t_cruz.add_column("DELTA", justify="left", style="red")
    t_cruz.add_column("H/L", justify="center", style="dim white")
    t_cruz.add_column("VOL $", justify="left", style="cyan")

    for K in sorted(agrupacion.keys()):
        c = calc_map.get(agrupacion[K].get('CALL'))
        p = calc_map.get(agrupacion[K].get('PUT'))

        # Si el mid es None o 0 en ambos, salteamos
        c_mid = get_safe_val(c, 'mid')
        p_mid = get_safe_val(p, 'mid')
        if c_mid == 0 and p_mid == 0: continue

        # --- PROCESAMIENTO SEGURO DE CALL ---
        c_h = get_safe_val(c, 'high')
        c_l = get_safe_val(c, 'low')
        c_hl = f"{c_h:.1f}/{c_l:.1f}" if c_h > 0 else "-"

        # --- PROCESAMIENTO SEGURO DE PUT ---
        p_h = get_safe_val(p, 'high')
        p_l = get_safe_val(p, 'low')
        p_hl = f"{p_h:.1f}/{p_l:.1f}" if p_h > 0 else "-"

        t_cruz.add_row(
            fmt_vol(get_safe_val(c, 'ev')),
            c_hl,
            f"{get_safe_val(c, 'delta'):.3f}",
            f"{get_safe_val(c, 'iv') * 100:.1f}%",
            f"{get_safe_val(c, 'bid'):.2f}",
            f"{get_safe_val(c, 'offer'):.2f}",
            f"{K:,.1f}",
            f"{get_safe_val(p, 'bid'):.2f}",
            f"{get_safe_val(p, 'offer'):.2f}",
            f"{get_safe_val(p, 'iv') * 100:.1f}%",
            f"{get_safe_val(p, 'delta'):.3f}",
            p_hl,
            fmt_vol(get_safe_val(p, 'ev'))
        )

    # --- TABLA ESTRATEGIAS (También blindada) ---
    t_est = Table(box=box.SIMPLE_HEAVY, expand=False)
    for col in ["ESTRATEGIA", "COSTO", "FINISH", "RATIO", "DELTA", "GAMMA", "THETA"]:
        t_est.add_column(col, justify="right")

    for estr in ESTRATEGIAS:
        neto, d_net, g_net, t_net, valida, s_c, s_v = 0, 0, 0, 0, True, 0, 0
        for pata in estr['patas']:
            sym = pata['symbol']
            if sym not in calc_map: valida = False; break
            dat, qty = calc_map[sym], pata['ratio']

            p_off = get_safe_val(dat, 'offer')
            p_bid = get_safe_val(dat, 'bid')
            p_last = get_safe_val(dat, 'last')

            px = (p_off if pata['lado'] == 'compra' else p_bid) if (p_off > 0 and p_bid > 0) else p_last
            if px == 0: valida = False; break

            strike_val = sym_to_strike.get(sym, 0)
            if pata['lado'] == 'compra':
                s_c = strike_val
            else:
                s_v = strike_val

            m = 1 if pata['lado'] == 'compra' else -1
            neto += px * qty * m
            d_net += get_safe_val(dat, 'delta') * qty * m
            g_net += get_safe_val(dat, 'gamma') * qty * m
            t_net += get_safe_val(dat, 'theta') * qty * m

        if valida:
            color = "red" if neto > 0 else "green"
            f_val = abs(s_c - s_v) - neto if (s_c > 0 and s_v > 0 and neto > 0) else 0
            t_est.add_row(
                estr['nombre'], f"[{color}]${neto:.2f}[/]", f"${f_val:.2f}" if f_val > 0 else "-",
                f"{(f_val / neto) * 100:.1f}%" if (f_val > 0 and neto > 0) else "-",
                f"{d_net:.3f}", f"{g_net:.4f}", f"{t_net:.2f}"
            )
        else:
            t_est.add_row(estr['nombre'], "[dim]Sin Liq[/]", "-", "-", "-", "-", "-")

    spot_str = f"${S:,.2f}" if S > 0 else "Cargando..."
    header = Panel(
        f"🎯 QUANT DASHBOARD GGAL | SPOT: [bold white]{spot_str}[/bold white] | HORA: {datetime.now().strftime('%H:%M:%S')}",
        style="blue")
    return Group(header, Columns([Panel(t_cruz, title="📊 CADENA DE OPCIONES (GGAL)", border_style="blue"),
                                  Panel(t_est, title="🛠️ ESTRATEGIAS", border_style="cyan")], expand=False))


class QuantApp(App):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def compose(self) -> ComposeResult:
        with ScrollableContainer(): yield Static(id="main_panel")

    def on_mount(self) -> None:
        self.set_interval(0.5, self.refresh_ui)

    def refresh_ui(self) -> None:
        S, calc_map, sym_to_strike = self.engine.get_view_data()
        if S > 0:
            self.query_one("#main_panel", Static).update(
                renderizar_tablas(S, calc_map, sym_to_strike, self.engine.agrupacion_strikes)
            )


# ==========================================
# 3. ORQUESTADOR
# ==========================================
def run():
    os.system('cls' if os.name == 'nt' else 'clear')
    if not inicializar_sesion(): return
    engine = OptionsEngine()
    if not engine.mapa_opciones:
        print("❌ No se encontraron opciones de GGAL en el mercado.")
        return
    ws_manager = WebSocketManager(engine)
    ws_manager.iniciar_ws(engine.get_tickers_suscripcion())
    QuantApp(engine).run()


if __name__ == "__main__":
    run()