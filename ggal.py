import yfinance as yf
import pandas as pd
import numpy as np
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import config


def actualizar_historico_ggal():
    print("🔐 Conectando con Google Sheets...")
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            config.GS_CREDS_FILE,
            config.GS_SCOPE
        )
        client = gspread.authorize(creds)
        spreadsheet = client.open(config.SPREADSHEET_NAME)

        try:
            worksheet = spreadsheet.worksheet("GGAL")
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title="GGAL", rows="100", cols="25")
    except Exception as e:
        print(f"❌ Error de acceso: {e}")
        return

    print("📉 Descargando y procesando métricas cuantitativas...")
    tickers = ["GGAL", "GGAL.BA"]
    # Bajamos historial suficiente para los cálculos de desvío
    df = yf.download(tickers, period="65d", interval="1d")

    # --- PROCESAMIENTO ---
    adr = df.xs('GGAL', axis=1, level=1)[['High', 'Low', 'Close']]
    local = df.xs('GGAL.BA', axis=1, level=1)[['High', 'Low', 'Close']]

    final_df = pd.concat([adr, local], axis=1)
    final_df.columns = ['ADR_High', 'ADR_Low', 'ADR_Close', 'LOCAL_High', 'LOCAL_Low', 'LOCAL_Close']

    # 1. Limpieza total (ffill)
    final_df = final_df.replace(0, np.nan).ffill().bfill()

    # 2. Retornos Simples (%)
    final_df['ADR_Pct'] = final_df['ADR_Close'].pct_change()
    final_df['LOCAL_Pct'] = final_df['LOCAL_Close'].pct_change()

    # 3. Retornos LOGARÍTMICOS (ln(1+r))
    final_df['ADR_Log'] = np.log(1 + final_df['ADR_Pct'])
    final_df['LOCAL_Log'] = np.log(1 + final_df['LOCAL_Pct'])

    # --- CÁLCULO DE VOLATILIDAD REALIZADA (40 ruedas) ---
    # Tomamos el desvío estándar de los log-retornos y anualizamos (sqrt 252)
    vol_adr = final_df['ADR_Log'].tail(40).std() * np.sqrt(252)
    vol_local = final_df['LOCAL_Log'].tail(40).std() * np.sqrt(252)

    # 4. Filtramos las últimas 40 y damos vuelta (Reciente primero)
    final_df_sub = final_df.tail(40).iloc[::-1].copy()
    final_df_sub = final_df_sub.reset_index()
    final_df_sub['Date'] = final_df_sub['Date'].dt.strftime('%Y-%m-%d')

    # --- CARGA A SHEETS ---
    # Definimos columnas para la tabla (A hasta la K)
    headers = [
        "Fecha",
        "ADR High", "ADR Low", "ADR Close", "ADR %", "ADR Log",
        "Local High", "Local Low", "Local Close", "Local %", "Local Log"
    ]

    columnas_finales = [
        'Date',
        'ADR_High', 'ADR_Low', 'ADR_Close', 'ADR_Pct', 'ADR_Log',
        'LOCAL_High', 'LOCAL_Low', 'LOCAL_Close', 'LOCAL_Pct', 'LOCAL_Log'
    ]

    matriz_final = [headers] + final_df_sub[columnas_finales].values.tolist()

    try:
        # Actualizamos el bloque A1:K41 (11 columnas)
        worksheet.update(values=matriz_final, range_name='A1')

        print(f"\n" + "=" * 40)
        print(f"✅ TABLA ACTUALIZADA (A1:K41)")
        print(f"=" * 40)
        print(f"📊 VOLATILIDAD REALIZADA (40R):")
        print(f"🔹 ADR (GGAL):   {vol_adr:.2%}")
        print(f"🔸 Local (BA):   {vol_local:.2%}")
        print(f"=" * 40)

    except Exception as e:
        print(f"❌ Error al escribir: {e}")


if __name__ == "__main__":
    actualizar_historico_ggal()