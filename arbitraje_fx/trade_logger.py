from pymongo import MongoClient
from datetime import datetime


class TradeLogger:
    def __init__(self, mongo_uri="mongodb://localhost:27017/"):
        self.client = MongoClient(mongo_uri)
        # Creamos una colección nueva para guardar el historial
        self.coleccion = self.client["Trading"]["ArbitrageHistory"]
        # Memoria RAM de las oportunidades que están "vivas" en este momento
        self.firmas_activas = set()

    def procesar_trades(self, trades_actuales):
        """
        Recibe la lista de trades del Matching Engine.
        Solo inserta en MongoDB los que son estadísticamente nuevos.
        """
        firmas_del_tick = set()

        for t in trades_actuales:
            # 1. Creamos la firma única (DNI del trade)
            # Redondeamos a 2 decimales el TC para evitar que variaciones de milésimas generen spam
            firma = f"{t['buy_asset']}_{round(t['buy_tc'], 2)}_{t['sell_asset']}_{round(t['sell_tc'], 2)}"
            firmas_del_tick.add(firma)

            # 2. Si la firma NO estaba activa, es una oportunidad fresquita. ¡A Mongo!
            if firma not in self.firmas_activas:
                doc = {
                    "timestamp": datetime.now(),
                    "buy_asset": t['buy_asset'],
                    "buy_tc": t['buy_tc'],
                    "sell_asset": t['sell_asset'],
                    "sell_tc": t['sell_tc'],
                    "volumen_usd": t['volumen_usd'],
                    "ganancia_ars": t['ganancia_ars'],
                    "spread_pct": round(t['spread_pct'], 4),
                    "firma": firma
                }
                # Insertamos en la DB
                try:
                    self.coleccion.insert_one(doc)
                except Exception as e:
                    pass  # Evitamos que un fallo de red tire abajo el HFT

        # 3. ACTUALIZACIÓN CRÍTICA:
        # Reemplazamos las firmas activas viejas por las de este segundo.
        # Si un trade se ejecutó y desapareció del order book, se borra de la memoria.
        self.firmas_activas = firmas_del_tick