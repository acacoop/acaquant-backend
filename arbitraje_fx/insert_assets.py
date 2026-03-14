from pymongo import MongoClient

client = MongoClient("mongodb://localhost:27017/")
coleccion = client["Trading"]["Assets"]

documento = {
    "asset": "CAC7O",
    "tipo": "RENTA FIJA",
    "lote": 100,
    "activo": True,
    "patas": {
        "ars": "MERV - XMEV - CAC7O - 24hs",
        "usd": "MERV - XMEV - CAC7D - 24hs"
    }
}

coleccion.update_one({"asset": documento["asset"]}, {"$set": documento}, upsert=True)
print("✅ Insertado.")