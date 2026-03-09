# arbitraje_fx/market_state.py

class MarketState:
    def __init__(self):
        self.data = {}

    def actualizar(self, message):
        """Procesa el mensaje crudo de pyRofex y actualiza el diccionario"""
        if message.get("type") != "Md":
            return

        symbol = message["instrumentId"]["symbol"]
        md = message["marketData"]

        if symbol not in self.data:
            self.data[symbol] = {'bid': 0, 'bid_size': 0, 'offer': 0, 'offer_size': 0}

        bids = md.get('BI', [])
        offs = md.get('OF', [])

        if bids:
            self.data[symbol]['bid'] = bids[0].get('price', 0)
            self.data[symbol]['bid_size'] = bids[0].get('size', 0)
        if offs:
            self.data[symbol]['offer'] = offs[0].get('price', 0)
            self.data[symbol]['offer_size'] = offs[0].get('size', 0)

    def obtener_puntas(self, symbol):
        """Devuelve las puntas o valores en cero si no hay data"""
        return self.data.get(symbol, {'bid': 0, 'bid_size': 0, 'offer': 0, 'offer_size': 0})