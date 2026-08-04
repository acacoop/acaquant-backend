import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

from core import pg_mirror

# Configuración de Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("VR_GGal")


def actualizar_historico_ggal():
    """Descarga GGAL (ADR) + GGAL.BA (local) de Yahoo, calcula la volatilidad
    realizada anualizada a 40 ruedas y persiste en SQL: la serie diaria en
    `mercado.options_vr` y la vol de referencia en `mercado.options_metadata`
    (fila type='vr_ggal')."""
    logger.info("📉 Descargando y procesando métricas de Yahoo Finance...")
    tickers = ["GGAL", "GGAL.BA"]

    try:
        # Descargamos historial
        df = yf.download(tickers, period="65d", interval="1d", progress=False)
        if df.empty:
            logger.warning("⚠️ No se recuperaron datos de Yahoo Finance.")
            return

        # --- PROCESAMIENTO ---
        adr = df.xs('GGAL', axis=1, level=1)[['High', 'Low', 'Close']]
        local = df.xs('GGAL.BA', axis=1, level=1)[['High', 'Low', 'Close']]

        final_df = pd.concat([adr, local], axis=1)
        final_df.columns = ['ADR_High', 'ADR_Low', 'ADR_Close', 'LOCAL_High', 'LOCAL_Low', 'LOCAL_Close']

        # Limpieza de nulos
        final_df = final_df.replace(0, np.nan).ffill().bfill()

        # Retornos Simples y Logarítmicos
        final_df['ADR_Pct'] = final_df['ADR_Close'].pct_change()
        final_df['LOCAL_Pct'] = final_df['LOCAL_Close'].pct_change()
        final_df['ADR_Log'] = np.log(1 + final_df['ADR_Pct'])
        final_df['LOCAL_Log'] = np.log(1 + final_df['LOCAL_Pct'])

        # Volatilidad Realizada Anualizada (40 ruedas)
        vol_adr = final_df['ADR_Log'].tail(40).std() * np.sqrt(252)
        vol_local = final_df['LOCAL_Log'].tail(40).std() * np.sqrt(252)

        # Preparamos los datos para insertar (Últimas 40 ruedas)
        final_df_sub = final_df.tail(40).copy()
        final_df_sub = final_df_sub.reset_index()

        # 2. Serie diaria → mercado.options_vr. Refresh atómico de toda la serie
        #    (TRUNCATE + INSERT en una transacción). PK = fecha (date). La vol de
        #    referencia NO se persiste acá — va a options_metadata vr_ggal, abajo.
        registros = final_df_sub.to_dict('records')
        rows = []
        for rec in registros:
            f = rec.get("Date")
            fecha = f.date() if hasattr(f, "date") else None
            if fecha is None:
                continue
            rows.append({"fecha": fecha, "data": pg_mirror.doc_iso(rec)})
        pg_mirror.replace_native("options_vr", rows)

        # 3. Vol de referencia (40R) → mercado.options_metadata, fila type='vr_ggal'.
        #    Mergea solo esa fila (||) → no toca la config (tasa/expiries), que la
        #    escriben el motor y el Manager. Es la ÚNICA escritura de la vol de
        #    referencia; la lee el dashboard vía get_opciones_meta.
        pg_mirror.merge_jsonb_native(
            "options_metadata", ["type"], ["vr_ggal"],
            {
                "vr_local":   round(vol_local, 4),
                "vr_adr":     round(vol_adr,   4),
                "updated_at": datetime.now(),
            },
        )

        print("\n" + "=" * 45)
        print("✅ SERIE PERSISTIDA EN SQL (mercado.options_vr)")
        print("=" * 45)
        print("📊 VOLATILIDAD REALIZADA (40R):")
        print(f"🔹 ADR (GGAL):   {vol_adr:.2%}")
        print(f"🔸 Local (BA):   {vol_local:.2%}")
        print("=" * 45)

    except Exception as e:
        logger.error(f"🔥 Error en el proceso: {e}")


if __name__ == "__main__":
    from core.job_runs import JobRunLogger
    with JobRunLogger("volatilidad_ggal"):
        actualizar_historico_ggal()