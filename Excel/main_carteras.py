import sys
import os

# Agregamos el path raíz para acceder a mongo_manager y config
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import holidays
from datetime import datetime, timedelta
from aunesa_api_manager import AunesaApiManager
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


def sincronizar_assets(df):
    """
    Sincroniza las unidades de Carteras hacia Assets.
    - Si la unidad ya existe: no hace nada
    - Si no existe: la agrega
    - Al final: elimina duplicados por unidad
    """
    client = get_mongo_client()
    collection = client["Valuaciones"]["Assets"]

    insertados = 0
    for _, row in df.iterrows():
        result = collection.update_one(
            {"unidad": row["unidad"]},
            {"$setOnInsert": {"unidad": row["unidad"]}},
            upsert=True
        )
        if result.upserted_id:
            insertados += 1

    # Deduplicación: si por alguna razón hay duplicados, se eliminan
    pipeline = [
        {"$group": {"_id": "$unidad", "ids": {"$push": "$_id"}, "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}}
    ]
    duplicados = list(collection.aggregate(pipeline))
    eliminados = 0
    for dup in duplicados:
        ids_a_borrar = dup["ids"][1:]  # Conserva el primero, borra el resto
        collection.delete_many({"_id": {"$in": ids_a_borrar}})
        eliminados += len(ids_a_borrar)

    client.close()
    return insertados, eliminados


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

            insertados, eliminados = sincronizar_assets(df_carteras)
            print(f"✅ MongoDB Valuaciones.Assets: {insertados} nuevos insertados, {eliminados} duplicados eliminados.")
        else:
            print("⚠️ No se recuperaron datos de la API.")

    except Exception as e:
        print(f"🔥 Error crítico en main: {e}")

    print("🏁 Proceso finalizado. Saliendo...")
    sys.exit()


if __name__ == "__main__":
    run()
