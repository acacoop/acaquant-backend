import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mongo_manager import get_mongo_client


def cargar_catalogo_bonos():
    """
    Se conecta a MongoDB Atlas y devuelve un diccionario indexado por el Ticker de Rofex.
    """
    client = get_mongo_client()
    coleccion = client["Trading"]["BondsMaster"]

    bonos_db = list(coleccion.find({}))
    catalogo = {}

    for bono in bonos_db:
        # Extraemos las dos patas de Rofex
        ticker_ars = bono['tickers']['ARS']
        ticker_usd = bono['tickers'].get('USD', '')  # Por si alguno no tiene pata D

        # Guardamos en el diccionario usando el Ticker ARS como llave
        catalogo[ticker_ars] = {
            "asset": bono['asset'],
            "emisor": bono['emisor'],
            "moneda_flujo": bono['moneda_flujo'],
            "vencimiento": bono['vencimiento'],
            "flujos": bono['flujos'],
            "moneda_cotizacion": "ARS"  # Sabemos que esta pata cotiza en ARS
        }

        # Si tiene pata USD, también la indexamos
        if ticker_usd:
            catalogo[ticker_usd] = {
                "asset": bono['asset'] + "D",
                "emisor": bono['emisor'],
                "moneda_flujo": bono['moneda_flujo'],
                "vencimiento": bono['vencimiento'],
                "flujos": bono['flujos'],
                "moneda_cotizacion": "USD"  # Sabemos que esta pata cotiza en USD
            }

    return catalogo