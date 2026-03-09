from pymongo import MongoClient


def cargar_pares_arbitraje():
    """Trae de la base de datos los pares activos para arbitrar plazos."""
    try:
        client = MongoClient("mongodb://localhost:27017/")
        db = client["Trading"]
        # Asumiendo que la colección se llama ArbitrajeMaster o similar
        coleccion = db["TasasAssets"]

        query = {"activo": True}
        pares = list(coleccion.find(query))
        client.close()
        return pares
    except Exception as e:
        print(f"❌ Error al conectar con MongoDB: {e}")
        return []