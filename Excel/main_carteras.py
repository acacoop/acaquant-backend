import sys
import os

# Agregamos el path raíz para acceder a mongo_manager y config
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import holidays
import pyRofex
from datetime import datetime, timedelta
from pymongo import ReplaceOne
from aunesa_api_manager import AunesaApiManager
from mongo_manager import get_mongo_client
from session_manager import inicializar_sesion

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


def actualizar_precios_mercado():
    """
    Para cada asset cuyo INSTRUMENTO contiene el patrón 'MERV - XMEV',
    consulta el LAST price via REST pyRofex y actualiza SOLO el campo
    'precio' en Carteras via $set. FCI sin INSTRUMENTO no son tocados.
    """
    import re
    client = get_mongo_client()
    db_val = client["Valuaciones"]

    assets = list(db_val["Assets"].find(
        {"INSTRUMENTO": {"$regex": "MERV - XMEV", "$options": "i"}},
        {"_id": 0, "unidad": 1, "INSTRUMENTO": 1}
    ))

    print(f"🔍 Assets con INSTRUMENTO MERV-XMEV encontrados: {len(assets)}")
    if not assets:
        client.close()
        return 0

    if not inicializar_sesion():
        print("⚠️ No se pudo inicializar sesión pyRofex. Se omite actualización de precios.")
        client.close()
        return 0

    actualizados = 0
    for asset in assets:
        instrumento = asset.get("INSTRUMENTO", "").strip()
        unidad = asset.get("unidad", "").strip()
        if not instrumento or not unidad:
            continue
        try:
            resp = pyRofex.get_market_data(
                ticker=instrumento,
                entries=[pyRofex.MarketDataEntry.LAST]
            )
            la = resp.get("marketData", {}).get("LA")
            if la and la.get("price"):
                precio = float(la["price"])
                db_val["Carteras"].update_many(
                    {"unidad": unidad},
                    {"$set": {"precio": precio}}
                )
                print(f"   ✅ {instrumento} → {precio}")
                actualizados += 1
            else:
                print(f"   ⚠️ Sin LA en respuesta para {instrumento}: {resp}")
        except Exception as e:
            print(f"   ❌ Error para {instrumento}: {e}")

    client.close()
    return actualizados


def guardar_en_mongo(df):
    """
    Upsert atómico de Valuaciones.Carteras usando la clave (id_cuenta, unidad).
    Evita la ventana de pérdida de datos que existía con delete_many + insert_many.
    """
    client = get_mongo_client()
    collection = client["Valuaciones"]["Carteras"]

    registros = df.to_dict(orient="records")
    if registros:
        ops = [
            ReplaceOne(
                {"id_cuenta": r.get("id_cuenta"), "unidad": r.get("unidad")},
                r,
                upsert=True
            )
            for r in registros
        ]
        collection.bulk_write(ops, ordered=False)

        # Eliminar filas que ya no vienen en el nuevo snapshot
        claves_actuales = [
            {"id_cuenta": r.get("id_cuenta"), "unidad": r.get("unidad")}
            for r in registros
        ]
        collection.delete_many({"$nor": claves_actuales})

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

            actualizados = actualizar_precios_mercado()
            print(f"✅ Precios de mercado actualizados: {actualizados} instrumentos.")
        else:
            print("⚠️ No se recuperaron datos de la API.")

    except Exception as e:
        print(f"🔥 Error crítico en main: {e}")

    print("🏁 Proceso finalizado. Saliendo...")
    sys.exit()


if __name__ == "__main__":
    run()
