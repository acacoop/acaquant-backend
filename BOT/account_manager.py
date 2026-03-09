import os
import logging
import pyRofex
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AccountManager")


class PortfolioManager:
    def __init__(self):
        self.account = os.getenv("ROFEX_ACCOUNT")
        if not self.account:
            logger.critical("🚨 ERROR: ROFEX_ACCOUNT no encontrado en .env")
            raise ValueError("ROFEX_ACCOUNT missing")

    def obtener_saldos(self):
        """
        Extrae saldos de ARS y USD D (MEP), separando por plazo CI ("0") y 48hs ("2").
        """
        try:
            res = pyRofex.get_account_report(account=self.account)
            if not res or res.get("status") != "OK":
                return {}

            reports = res.get("accountData", {}).get("detailedAccountReports", {})
            saldos = {}

            # Recorremos los plazos (0=CI, 2=48hs)
            for plazo in ["0", "2"]:
                saldos[plazo] = {
                    "ARS": {"available": 0.0, "consumed": 0.0},
                    "MEP": {"available": 0.0, "consumed": 0.0}  # "USD D" en el JSON
                }

                if plazo in reports:
                    balances = reports[plazo].get("currencyBalance", {}).get("detailedCurrencyBalance", {})

                    if "ARS" in balances:
                        saldos[plazo]["ARS"]["available"] = float(balances["ARS"].get("available", 0.0))
                        saldos[plazo]["ARS"]["consumed"] = float(balances["ARS"].get("consumed", 0.0))

                    if "USD D" in balances:
                        saldos[plazo]["MEP"]["available"] = float(balances["USD D"].get("available", 0.0))
                        saldos[plazo]["MEP"]["consumed"] = float(balances["USD D"].get("consumed", 0.0))

            return saldos
        except Exception as e:
            logger.error(f"Error parseando saldos: {e}")
            return {}

    def obtener_tenencia(self, asset_reference):
        """
        Busca la tenencia de un activo puntual (útil para cuando el bot quiere vender algo específico).
        """
        try:
            res = pyRofex.get_account_position(account=self.account)
            if not res or res.get("status") != "OK":
                return 0.0

            posiciones = res.get("positions", [])
            tenencia_total = 0.0

            for pos in posiciones:
                sym_ref = pos.get("instrument", {}).get("symbolReference", "")
                sym_24 = pos.get("symbol", "")
                sym_ci = pos.get("tradingSymbol", "")

                if asset_reference in [sym_ref, sym_24, sym_ci]:
                    b_size = float(pos.get("buySize", 0.0))
                    s_size = float(pos.get("sellSize", 0.0))
                    tenencia_total += (b_size - s_size)

            return tenencia_total
        except Exception as e:
            logger.error(f"Error parseando tenencia: {e}")
            return 0.0

    def obtener_todas_las_tenencias(self):
        """
        Barre toda la cuenta y devuelve un diccionario con todo lo que tiene nominales netos > 0.
        SIN SESGOS. Lo que hay, es lo que devuelve.
        """
        try:
            res = pyRofex.get_account_position(account=self.account)
            if not res or res.get("status") != "OK":
                return {}

            posiciones = res.get("positions", [])
            cartera = {}

            for pos in posiciones:
                # Usamos el symbolReference como llave principal unificadora
                sym_ref = pos.get("instrument", {}).get("symbolReference", "DESCONOCIDO")
                b_size = float(pos.get("buySize", 0.0))
                s_size = float(pos.get("sellSize", 0.0))
                neta = b_size - s_size

                if neta != 0:
                    # Sumamos por si el broker manda el mismo activo en varias líneas
                    cartera[sym_ref] = cartera.get(sym_ref, 0.0) + neta

            return cartera
        except Exception as e:
            logger.error(f"Error parseando cartera completa: {e}")
            return {}


# ==========================================
# TEST DINÁMICO
# ==========================================
if __name__ == "__main__":
    from session_manager import inicializar_sesion

    if inicializar_sesion():
        pm = PortfolioManager()

        print("\n💰 SALDOS EXTRAÍDOS (Dinámico):")
        saldos = pm.obtener_saldos()
        for plazo, datas in saldos.items():
            nombre_plazo = "CI (0)" if plazo == "0" else "48hs (2)"
            print(f"  [{nombre_plazo}]")
            print(f"    - ARS: Disp: ${datas['ARS']['available']:,.2f} | Consumido: ${datas['ARS']['consumed']:,.2f}")
            print(
                f"    - MEP: Disp: U$S {datas['MEP']['available']:,.2f} | Consumido: U$S {datas['MEP']['consumed']:,.2f}")

        print("\n📦 TENENCIAS EXTRAÍDAS (Dinámico):")
        mi_cartera = pm.obtener_todas_las_tenencias()

        if not mi_cartera:
            print("  ➤ No hay posiciones abiertas actualmente.")
        else:
            for activo, cantidad in mi_cartera.items():
                print(f"  ➤ {activo}: {cantidad} nominales netos")