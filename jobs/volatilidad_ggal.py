import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

from core.mongo import MongoManager, get_mongo_client

# Configuración de Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("VR_GGal")


def actualizar_historico_ggal_mongo():
    # 1. Inicializamos tu manager apuntando a la colección correcta
    # Usamos la DB 'Opciones' y la colección 'VR-GGal' según tu imagen
    mongo = MongoManager(db_name="Opciones", collection_name="VR-GGal")

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

        # 2. Limpieza previa de la colección para refrescar datos
        mongo.collection.delete_many({})

        # 3. Inserción de registros
        registros = final_df_sub.to_dict('records')
        mongo.collection.insert_many(registros)

        # 4. Insertamos un documento de resumen con las volatilidades calculadas
        resumen = {
            "type": "SUMMARY_METRICS",
            "updated_at": datetime.now(),
            "vol_40r_adr": round(vol_adr, 4),
            "vol_40r_local": round(vol_local, 4)
        }
        mongo.collection.insert_one(resumen)

        # 5. Upsert en Metadata para que el dashboard de Streamlit lo lea
        client = get_mongo_client()
        meta_col = client["Opciones"]["Metadata"]
        meta_col.update_one(
            {"type": "vr_ggal"},
            {"$set": {
                "vr_local":   round(vol_local, 4),
                "vr_adr":     round(vol_adr,   4),
                "updated_at": datetime.now(),
            }},
            upsert=True
        )

        print("\n" + "=" * 45)
        print("✅ DATOS PERSISTIDOS EN MONGO (VR-GGal)")
        print("=" * 45)
        print("📊 VOLATILIDAD REALIZADA (40R):")
        print(f"🔹 ADR (GGAL):   {vol_adr:.2%}")
        print(f"🔸 Local (BA):   {vol_local:.2%}")
        print("=" * 45)

    except Exception as e:
        logger.error(f"🔥 Error en el proceso: {e}")


if __name__ == "__main__":
    actualizar_historico_ggal_mongo()