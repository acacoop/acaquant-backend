import pyRofex
from datetime import datetime
from session_manager import inicializar_sesion

# --- CAMBIÁ EL TICKER ACÁ ---
TICKER = "MERV - XMEV - GFGC79515A - 24hs"


# ----------------------------

def run():
    if not inicializar_sesion():
        return

    try:
        # 1. Obtenemos Detalles (Strike y Fecha)
        res_details = pyRofex.get_instrument_details(TICKER)

        # 2. Obtenemos Market Data (Open Interest)
        # Pedimos solo la entrada de OPEN_INTEREST para ser eficientes
        res_md = pyRofex.get_market_data(ticker=TICKER, entries=[pyRofex.MarketDataEntry.OPEN_INTEREST])

        if res_details['status'] == 'OK':
            inst = res_details['instrument']

            # Extracción de datos estáticos
            strike = inst.get('strike')
            raw_date = inst.get('maturityDate')
            fecha_dt = datetime.strptime(raw_date, "%Y%m%d")
            fecha_excel = fecha_dt.strftime("%d/%m/%Y")

            # Extracción de Open Interest
            # El OI viene dentro de marketData -> OI -> quantity (o price según la versión)
            oi = 0
            if res_md['status'] == 'OK' and res_md['marketData'].get('OI'):
                oi = res_md['marketData']['OI'].get('quantity', 0)

            # 3. Salida limpia para copiar y pegar
            print("\n" + "—" * 30)
            print(f"TICKER:  {TICKER}")
            print(f"STRIKE:  {strike}")
            print(f"FECHA:   {fecha_excel}")
            print(f"OI:      {int(oi)}")  # Lo pasamos a entero para Excel
            print("—" * 30)

        else:
            print(f"❌ No se encontró: {TICKER}")

    except Exception as e:
        print(f"⚠️ Error: {e}")


if __name__ == "__main__":
    run()