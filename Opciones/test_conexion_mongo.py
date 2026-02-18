import os
from pymongo import MongoClient
from dotenv import load_dotenv

# 1. Cargamos las variables del archivo .env que está en la raíz de TRD-FX
load_dotenv()


def conectar_atlas():
    # 2. Extraemos las credenciales del entorno
    user = os.getenv("MONGO_USER")
    password = os.getenv("MONGO_PASS")
    cluster = os.getenv("MONGO_CLUSTER")
    db_name = os.getenv("MONGO_DB_NAME")
    app_name = os.getenv("MONGO_APP_NAME")

    # 3. Construimos la URI con el formato que requiere Atlas
    # Usamos f-strings para insertar las variables del .env
    uri = f"mongodb+srv://{user}:{password}@{cluster}/?retryWrites=true&w=majority&appName={app_name}"

    try:
        # 4. Intentamos la conexión
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)

        # Forzamos una llamada al servidor para validar la conexión real
        client.admin.command('ping')
        print("✅ ¡Conexión exitosa a MongoDB Atlas!")

        # 5. Mostramos qué bases de datos hay disponibles
        # Deberías ver 'acaquant' y 'sample_mflix' si ya se crearon
        print(f"Bases de datos detectadas: {client.list_database_names()}")

        return client[db_name]

    except Exception as e:
        print(f"❌ Error al conectar: {e}")
        print("\n💡 Tip: Verificá si tu IP está habilitada en 'Network Access' dentro de Atlas.")
        return None


if __name__ == "__main__":
    db = conectar_atlas()
    if db is not None:
        print(f"Ya podés empezar a trabajar con la base de datos: {db.name}")