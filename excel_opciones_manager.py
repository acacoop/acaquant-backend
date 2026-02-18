import gspread
import config
import logging
from oauth2client.service_account import ServiceAccountCredentials
from calculos_cuantitativos import (
    calc_intrinseco, calc_extrinseco, find_iv,
    bs_delta, bs_gamma, bs_vega, bs_theta
)
from config_GGAL_OPCIONES import VENCIMIENTOS_INFO

logger = logging.getLogger("TradingBot")


class ExcelOpcionesManager:
    def __init__(self, tickers, mapa):
        self.tickers = tickers
        self.mapa = mapa
        self.creds = ServiceAccountCredentials.from_json_keyfile_name(config.GS_CREDS_FILE, config.GS_SCOPE)
        self.client = None
        self.sheet = None
        self.conectar()

    def conectar(self):
        try:
            self.client = gspread.authorize(self.creds)
            self.sheet = self.client.open(config.SPREADSHEET_NAME).worksheet("BookOpciones")
            return True
        except Exception as e:
            logger.error(f"Error de conexion Excel: {e}")
            return False

    def escribir_data(self, market_data):
        matriz = []

        # 1. Obtener precio SPOT (GGAL)
        spot_symbol = "MERV - XMEV - GGAL - 24hs"
        spot_info = market_data.get(spot_symbol, {})
        S = spot_info.get('last', 0) or spot_info.get('bid', 0)

        r = 0.27  # Tasa de interés (27% según tu última actualización)

        for ticker in self.tickers:
            data = market_data.get(ticker, {
                'bid': 0, 'bid_size': 0, 'offer': 0, 'offer_size': 0, 'last': 0, 'last_size': 0
            })

            info = self.mapa.get(ticker, {})
            tipo = info.get('tipo', 'ACCION')
            K = info.get('strike', 0)
            vto_key = info.get('vencimiento')

            iv, delta, gamma, vega, theta, vi, ve = 0, 0, 0, 0, 0, 0, 0
            mny = "-"

            if tipo != 'ACCION' and S > 0:
                # --- CALCULO MID PRICE ---
                if data['bid'] > 0 and data['offer'] > 0:
                    precio_ref = (data['bid'] + data['offer']) / 2
                else:
                    precio_ref = data['last']

                T = VENCIMIENTOS_INFO.get(vto_key, 0) / 365
                vi = calc_intrinseco(S, K, tipo)
                mny = "ITM" if (tipo == 'CALL' and S > K) or (tipo == 'PUT' and S < K) else "OTM"

                # --- SOLUCIÓN IV 0% ---
                # Si el precio es menor o igual al intrínseco, forzamos un mínimo técnico
                # para que find_iv pueda trabajar.
                precio_para_iv = precio_ref
                if precio_para_iv <= vi:
                    precio_para_iv = vi + 0.1  # Le damos 10 centavos de "aire"

                if T > 0:
                    iv = find_iv(precio_para_iv, S, K, T, r, tipo)
                    ve = precio_para_iv - vi  # Valor extrínseco recalculado

                    if iv > 0:
                        delta = bs_delta(S, K, T, r, iv, tipo)
                        gamma = bs_gamma(S, K, T, r, iv)
                        vega = bs_vega(S, K, T, r, iv)
                        theta = bs_theta(S, K, T, r, iv, tipo)

            fila = [
                ticker, tipo, data['bid_size'], data['bid'], data['offer'],
                data['offer_size'], data['last'], data['last_size'], mny,
                round(vi, 1), round(ve, 1), K, f"{round(iv * 100, 1)}%",
                round(delta, 3), round(gamma, 4), round(vega, 2), round(theta, 2), vto_key
            ]
            matriz.append(fila)

        try:
            self.sheet.update(range_name='A2', values=matriz)
            return True
        except Exception:
            self.conectar()
            return False