# arbitraje_fx/mongo_assets.py
from pymongo import MongoClient


class AssetManager:
    def __init__(self, mongo_uri="mongodb://localhost:27017/"):
        self.client = MongoClient(mongo_uri)
        self.coleccion = self.client["Trading"]["Assets"]

    def obtener_pares_activos(self, padron_rofex):
        """
        Lee los activos TRUE de Mongo, cruza contra el padrón del broker
        y devuelve la lista de diccionarios validada.
        """
        pares_validados = []
        tickers_a_suscribir = set()

        # Traemos solo los activos marcados como TRUE en Mongo
        docs = list(self.coleccion.find({"activo": True}))

        for doc in docs:
            ars = doc["patas"]["ars"]
            usd = doc["patas"]["usd"]

            # Validación: ¿Existen ambas patas en el broker HOY?
            if ars in padron_rofex and usd in padron_rofex:
                pares_validados.append({
                    "asset": doc["asset"],
                    "ars": ars,
                    "usd": usd,
                    "lote": doc.get("lote", 1)
                })
                tickers_a_suscribir.add(ars)
                tickers_a_suscribir.add(usd)
            else:
                faltantes = []
                if ars not in padron_rofex: faltantes.append(ars)
                if usd not in padron_rofex: faltantes.append(usd)
                print(f"⚠️ OMITIDO: {doc['asset']:<6} -> No listado hoy: {faltantes}")

        return pares_validados, list(tickers_a_suscribir)