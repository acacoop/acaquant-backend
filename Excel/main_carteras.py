import sys
import os
import holidays
from datetime import datetime, timedelta
from aunesa_api_manager import AunesaApiManager

# Agregamos el path raíz para acceder a mongo_manager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mongo_manager import get_mongo_client

# Definición de las cuentas
CUENTAS_OBJETIVO = ["100", "255", "101", "163"]


def obtener_fechas_habiles():
    """
    Calcula el T+2 (próximo próximo día hábil) para el 'Desde'.
    Salta fines de semana y feriados.
    """
    arg_holidays = holidays.Argentina()

    def proximo_habil(fecha_ref):
        proximo = fecha_ref + timedelta(days=1)
        while proximo.weekday() >= 5 or proximo in arg_holidays:
            proximo += timedelta(days=1)
        return proximo

    hoy = datetime.now()
    t_mas_1 = proximo_habil(hoy)
    t_mas_2 = proximo_habil(t_mas_1)

    return t_mas_2.strftime("%d/%m/%Y"), ""


def guardar_en_mongo(df):
    """
    Borra todo lo que haya en Valuaciones.Carteras y guarda los nuevos datos.
    Nunca hay duplicados: siempre es un reemplazo total.
    """
    client = get_mongo_client()
    collection = client["Valuaciones"]["Carteras"]

    # Overwrite: borramos todo y reinsertamos
    collection.delete_many({})

    registros = df.to_dict(orient="records")
    if registros:
        collection.insert_many(registros)

    client.close()
    return len(registros)


def run():
    print("🚀 Iniciando Actualización Única de Carteras...")

    fecha_desde, fecha_hasta = obtener_fechas_habiles()
    print(f"📅 Consulta Desde (T+2): {fecha_desde}")

    api_manager = AunesaApiManager()

    try:
        df_carteras = api_manager.consultar_cuentas(
            CUENTAS_OBJETIVO,
            desde=fecha_desde,
            hasta=fecha_hasta
        )

        if df_carteras is not None and not df_carteras.empty:
            print(f"📊 Registros consolidados: {len(df_carteras)}")

            cantidad = guardar_en_mongo(df_carteras)
            print(f"✅ MongoDB Valuaciones.Carteras actualizado: {cantidad} registros.")
        else:
            print("⚠️ No se recuperaron datos de la API.")

    except Exception as e:
        print(f"🔥 Error crítico en main: {e}")

    print("🏁 Proceso finalizado. Saliendo...")
    sys.exit()


if __name__ == "__main__":
    run()
