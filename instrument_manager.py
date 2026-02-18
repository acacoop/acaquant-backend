import pyRofex
import pandas as pd
import config


def obtener_ons_filtradas():
    """
    Descarga, filtra y clasifica los instrumentos (ARS vs MEP).
    """
    try:
        print("🔍 Descargando instrumentos desde Matba Rofex...")
        instruments = pyRofex.get_detailed_instruments()

        if not instruments or "instruments" not in instruments:
            return None

        df_instruments = pd.DataFrame(instruments["instruments"])

        # 1. Filtros base (CFI, Selección, Columnas, Moneda y Plazo)
        ons = df_instruments[df_instruments['cficode'] == 'DBXXFR'].copy()
        df_sel = ons[ons['underlying'].isin(config.MIS_SELECCIONADOS)].copy()
        df_sel = df_sel[config.COLUMNAS_MANTENER].copy()
        df_sel = df_sel[df_sel['currency'].isin(["USD", "ARS"])]
        df_sel = df_sel[df_sel['securityDescription'].str.contains("24hs")]

        # --- CLASIFICACIÓN DE TICKERS ---
        # 2. Extraemos el ticker (Ej: "MERV - XMEV - YPFD - 24hs" -> "YPFD")
        # Usamos split por '-' y tomamos el tercer elemento (índice 2)
        df_sel['ticker'] = df_sel['securityDescription'].str.split('-').str[2].str.strip()

        # 3. Identificamos la clase (MEP, Cable o ARS)
        # Lógica: termina en D -> MEP, termina en C -> Cable, resto -> ARS
        df_sel['clase'] = df_sel['ticker'].apply(
            lambda x: 'MEP' if x.endswith('D') else ('Cable' if x.endswith('C') else 'ARS')
        )

        # 4. Filtro final: Nos quedamos solo con MEP y ARS
        df_sel = df_sel[df_sel['clase'].isin(["MEP", "ARS"])].copy()

        print(f"✅ Clasificación completa: {len(df_sel)} instrumentos listos (ARS/MEP).")
        return df_sel.reset_index(drop=True)

    except Exception as e:
        print(f"❌ Error en la clasificación de instrumentos: {e}")
        return None

if __name__ == "__main__":
    # Importante: Para probar esto solo, tendrías que haber llamado a inicializar_sesion() antes
    # Esto es solo una estructura modular
    pass