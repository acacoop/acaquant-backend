"""
crear_indices.py — Crea índices en MongoDB para mejorar performance.

Seguro de ejecutar: no modifica datos, solo agrega índices de búsqueda.
Idempotente: si el índice ya existe, no hace nada.

Uso:
    python crear_indices.py
"""

from mongo_manager import get_mongo_client


def main():
    client = get_mongo_client()

    trading = client["Trading"]
    valuaciones = client["Valuaciones"]

    indices = [
        (trading["TimeSales"],         [("ticker", 1), ("timestamp", -1)],                    "TimeSales: ticker + timestamp"),
        (trading["TimeSales"],         [("ticker", 1), ("duration", 1), ("timestamp", -1)],   "TimeSales: ticker + duration + timestamp"),
        (trading["TimeSales"],         [("ticker", 1), ("TEA", 1), ("timestamp", -1)],         "TimeSales: ticker + TEA + timestamp"),
        (trading["TimeSales"],         [("ticker", 1), ("TEM", 1), ("timestamp", -1)],         "TimeSales: ticker + TEM + timestamp"),
        (trading["TimeSales"],         [("ticker", 1), ("paridad", 1), ("timestamp", -1)],     "TimeSales: ticker + paridad + timestamp"),
        (trading["ForwardsHistorico"], [("curva", 1), ("fecha", -1)],                          "ForwardsHistorico: curva + fecha"),
        (trading["BreakevensHistorico"],[("fecha", -1)],                                       "BreakevensHistorico: fecha"),
        (trading["MarketSnapshot"],    [("ticker", 1)],                                        "MarketSnapshot: ticker"),
        (valuaciones["AuM"],           [("fecha_snapshot", 1)],                                "AuM: fecha_snapshot"),
    ]

    print(f"Creando {len(indices)} índices...\n")
    for col, keys, desc in indices:
        try:
            col.create_index(keys)
            print(f"  OK       {desc}")
        except Exception as e:
            print(f"  SKIP     {desc}  ({e})")

    print("\nTodos los índices creados correctamente.")
    client.close()


if __name__ == "__main__":
    main()
