import os
from pymongo import MongoClient
from datetime import datetime
from dotenv import load_dotenv

# 1. Cargar configuración
load_dotenv()


def insertar_activo_nube(vencimiento_str, strike, tipo_opcion, tipo_ejercicio, ticker, Symbol, subyacente, contrato):
    # Credenciales hardcodeadas o desde .env
    user = os.getenv("MONGO_USER")
    password = os.getenv("MONGO_PASS")
    cluster = os.getenv("MONGO_CLUSTER")
    app_name = os.getenv("MONGO_APP_NAME")

    uri = f"mongodb+srv://{user}:{password}@{cluster}/?retryWrites=true&w=majority&appName={app_name}"

    try:
        client = MongoClient(uri)
        # Apuntamos a la base Opciones y colección Assets
        coleccion = client['Opciones']['Assets']

        # Maqueta del documento con formatos correctos
        documento = {
            "fecha_vencimiento": datetime.strptime(vencimiento_str, "%Y/%m/%d"),
            "strike": float(strike),
            "tipo_opcion": tipo_opcion,
            "tipo_ejercicio": tipo_ejercicio,
            "ticker": ticker.upper(),
            "Symbol": Symbol.upper(),
            "subyacente": subyacente.upper(),
            "contrato": str(contrato)
        }

        resultado = coleccion.insert_one(documento)
        print(f"✅ Activo en la nube con ID: {resultado.inserted_id}")

    except Exception as e:
        print(f"❌ Error en la nube: {e}")


# --- Ejemplo de uso ---
if __name__ == "__main__":
    insertar_activo_nube(
        vencimiento_str="2026/02/20",
        strike=8530,
        tipo_opcion="Call",
        tipo_ejercicio="Americana",
        Symbol="GFGC8530FE",
        subyacente="GGAL",
        ticker="MERV - XMEV - GFGC8530FE - 24hs",
        contrato="8530FE"
    )