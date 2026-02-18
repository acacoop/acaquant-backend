import pandas as pd
import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()


def importar_opciones(nombre_archivo):
    ruta = os.path.join(r"C:\Users\nicolas.mollo\Desktop\FX-TRADING\Opciones", nombre_archivo)

    try:
        df = pd.read_csv(ruta)

        # Formateo de números (limpia puntos de miles y comas decimales)
        cols_num = ['Executed Value', 'Executed Size', 'Cumulative Qty']
        for col in cols_num:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace('.', '', regex=False).str.replace(',', '.', regex=False),
                errors='coerce')

        # Formateo de fecha
        df['Transact Time'] = pd.to_datetime(df['Transact Time'], format='%d/%m/%Y-%H:%M:%S', errors='coerce')

        df = df.dropna(subset=cols_num + ['Transact Time'])

        # Conexión y subida
        uri = f"mongodb+srv://{os.getenv('MONGO_USER')}:{os.getenv('MONGO_PASS')}@{os.getenv('MONGO_CLUSTER')}/?appName={os.getenv('MONGO_APP_NAME')}"
        client = MongoClient(uri)
        coleccion = client['Opciones']['Data']

        if not df.empty:
            res = coleccion.insert_many(df.to_dict(orient='records'))
            print(f"✅ Subidos {len(res.inserted_ids)} registros de {nombre_archivo}")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    importar_opciones("GFGC8530FE.csv")


