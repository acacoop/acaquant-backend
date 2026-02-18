import os
import pandas as pd
from pymongo import MongoClient
from dotenv import load_dotenv

# 1. Cargamos configuración del .env
load_dotenv()


def conectar_nube():
    """Establece conexión con el clúster ACAQuant en Atlas."""
    user = os.getenv("MONGO_USER")
    password = os.getenv("MONGO_PASS")
    cluster = os.getenv("MONGO_CLUSTER")
    app_name = os.getenv("MONGO_APP_NAME")

    uri = f"mongodb+srv://{user}:{password}@{cluster}/?retryWrites=true&w=majority&appName={app_name}"

    client = MongoClient(uri)
    return client['Opciones']['Data']  # Acceso directo a la colección


def consumir_datos_ejecucion(symbol_filter=None):
    """
    Recupera los datos de la nube.
    Ya no hace falta limpiar comas porque los datos ya son numéricos en Atlas.
    """
    coleccion = conectar_nube()

    query = {}
    if symbol_filter:
        query = {"Symbol": symbol_filter}

    # Traemos los documentos
    cursor = coleccion.find(query)
    datos = list(cursor)

    if not datos:
        print(f"⚠️ No se encontraron datos en la nube para: {symbol_filter if symbol_filter else 'todos'}")
        return None

    # 2. Transformación a DataFrame
    df = pd.DataFrame(datos)

    # NOTA: Como ya los subimos como FLOAT y DATETIME, Pandas los reconoce
    # automáticamente. No hace falta hacer .str.replace().

    return df


# --- Ejemplo de ejecución ---
if __name__ == "__main__":
    print("🚀 Consumiendo datos desde MongoDB Atlas (Nube)...")

    # Ejemplo con tu símbolo real
    df_opciones = consumir_datos_ejecucion(symbol_filter="GFGC8230FE")

    if df_opciones is not None:
        print("\n📊 Resumen de Ejecuciones desde la Nube:")
        # Mostramos las columnas procesadas como números
        columnas_interes = ['Symbol', 'Executed Value', 'Executed Size', 'Transact Time']
        print(df_opciones[columnas_interes].tail())

        # 3. Cálculos de Quant directos (sin conversiones previas)
        # Multiplicamos los Floats nativos de la base
        df_opciones['Notional'] = df_opciones['Executed Value'] * df_opciones['Executed Size']
        volumen_total = df_opciones['Notional'].sum()

        print(f"\n💰 Volumen Total Operado (Notional): ${volumen_total:,.2f}")

        # Al ser Datetime real, podrías incluso filtrar por hora:
        # print(df_opciones[df_opciones['Transact Time'].dt.hour > 12])