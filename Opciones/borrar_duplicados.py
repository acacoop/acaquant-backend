import os
from pymongo import MongoClient
from dotenv import load_dotenv

# 1. Cargar configuración del .env
load_dotenv()


def limpiar_duplicados_nube():
    # Construcción de la URI para Atlas
    uri = f"mongodb+srv://{os.getenv('MONGO_USER')}:{os.getenv('MONGO_PASS')}@{os.getenv('MONGO_CLUSTER')}/?appName={os.getenv('MONGO_APP_NAME')}"

    try:
        client = MongoClient(uri)
        coleccion = client['Opciones']['Data']  #

        print("🔍 Analizando registros idénticos en la nube...")

        # 2. Agrupación por los 5 campos de negocio
        pipeline = [
            {
                "$group": {
                    "_id": {
                        "Symbol": "$Symbol",
                        "Time": "$Transact Time",
                        "Value": "$Executed Value",
                        "Size": "$Executed Size",
                        "Qty": "$Cumulative Qty"
                    },
                    "ids": {"$push": "$_id"},
                    "cantidad": {"$sum": 1}
                }
            },
            {
                "$match": {"cantidad": {"$gt": 1}}  # Solo los que se repiten
            }
        ]

        duplicados = list(coleccion.aggregate(pipeline))

        if not duplicados:
            print("✨ No se encontraron registros duplicados. La base de datos está limpia.")
            return

        # 3. Preparar lista de IDs para borrar (dejamos siempre 1 original)
        ids_para_borrar = []
        for grupo in duplicados:
            # El primer ID de la lista se queda, los demás (índice 1 en adelante) se borran
            ids_para_borrar.extend(grupo['ids'][1:])

        print(f"⚠️ Se encontraron {len(duplicados)} grupos de duplicados.")
        print(f"🚀 Procediendo a eliminar {len(ids_para_borrar)} registros sobrantes...")

        # 4. Ejecución del borrado masivo
        resultado = coleccion.delete_many({"_id": {"$in": ids_para_borrar}})

        print(f"✅ Éxito: Se eliminaron {resultado.deleted_count} duplicados correctamente.")

    except Exception as e:
        print(f"❌ Error durante el proceso: {e}")


if __name__ == "__main__":
    limpiar_duplicados_nube()