"""
crear_indices.py — Crea índices en MongoDB para mejorar performance.

Seguro de ejecutar: no modifica datos, solo agrega índices de búsqueda.
Idempotente: si el índice ya existe, no hace nada.

Uso:
    python crear_indices.py
"""

from core.mongo import get_mongo_client


def main():
    client = get_mongo_client()

    trading    = client["Trading"]
    valuaciones = client["Valuaciones"]
    cashflow   = client["CashFlow"]
    opciones   = client["Opciones"]

    indices = [
        # ── Trading.TimeSales ─────────────────────────────────────────────
        (trading["TimeSales"], [("ticker", 1), ("timestamp", -1)],
            "TimeSales: ticker + timestamp"),
        (trading["TimeSales"], [("ticker", 1), ("duration", 1), ("timestamp", -1)],
            "TimeSales: ticker + duration + timestamp"),
        (trading["TimeSales"], [("ticker", 1), ("TEA", 1), ("timestamp", -1)],
            "TimeSales: ticker + TEA + timestamp"),
        (trading["TimeSales"], [("ticker", 1), ("TEM", 1), ("timestamp", -1)],
            "TimeSales: ticker + TEM + timestamp"),
        (trading["TimeSales"], [("ticker", 1), ("paridad", 1), ("timestamp", -1)],
            "TimeSales: ticker + paridad + timestamp"),

        # ── Trading.MarketSnapshot ────────────────────────────────────────
        (trading["MarketSnapshot"], [("ticker", 1)],
            "MarketSnapshot: ticker"),

        # ── Trading.ForwardsHistorico / BreakevensHistorico ───────────────
        (trading["ForwardsHistorico"],  [("curva", 1), ("fecha", -1)],
            "ForwardsHistorico: curva + fecha"),
        (trading["BreakevensHistorico"], [("fecha", -1)],
            "BreakevensHistorico: fecha"),

        # ── Trading.CER (usado por main_curvas.py para valuación) ─────────
        (trading["CER"], [("fecha", 1)],
            "CER: fecha"),

        # ── Trading.Curvas (filtros por curva y ticker_corto) ─────────────
        (trading["Curvas"], [("curva", 1)],
            "Curvas: curva"),
        (trading["Curvas"], [("ticker_corto", 1)],
            "Curvas: ticker_corto"),

        # ── Valuaciones.AuM ───────────────────────────────────────────────
        (valuaciones["AuM"], [("fecha_snapshot", 1)],
            "AuM: fecha_snapshot"),
        (valuaciones["AuM"], [("unidad", 1), ("fecha_snapshot", -1)],
            "AuM: unidad + fecha_snapshot"),
        (valuaciones["AuM"], [("id_cuenta", 1), ("fecha_snapshot", -1)],
            "AuM: id_cuenta + fecha_snapshot"),

        # ── Valuaciones.AuMResumen ────────────────────────────────────────
        (valuaciones["AuMResumen"], [("id_cuenta", 1), ("unidad", 1), ("fecha_snapshot", -1)],
            "AuMResumen: id_cuenta + unidad + fecha_snapshot"),
        (valuaciones["AuMResumen"], [("CARTERA", 1), ("fecha_snapshot", -1)],
            "AuMResumen: CARTERA + fecha_snapshot"),
        (valuaciones["AuMResumen"], [("EMISOR", 1), ("fecha_snapshot", -1)],
            "AuMResumen: EMISOR + fecha_snapshot"),

        # ── Valuaciones.Carteras ──────────────────────────────────────────
        (valuaciones["Carteras"], [("id_cuenta", 1), ("unidad", 1)],
            "Carteras: id_cuenta + unidad"),

        # ── Valuaciones.Assets ────────────────────────────────────────────
        (valuaciones["Assets"], [("unidad", 1)],
            "Assets: unidad (unique)", {"unique": True}),
        (valuaciones["Assets"], [("EMISOR", 1), ("CARTERA", 1)],
            "Assets: EMISOR + CARTERA"),

        # ── CashFlow.Flujo ────────────────────────────────────────────────
        (cashflow["Flujo"], [("contraparte", 1), ("moneda", 1)],
            "Flujo: contraparte + moneda"),
        (cashflow["Flujo"], [("concertacion", -1)],
            "Flujo: concertacion"),
        (cashflow["Flujo"], [("boleto", 1)],
            "Flujo: boleto (unique int)",
            {"unique": True, "partialFilterExpression": {"boleto": {"$type": "int"}}}),

        # ── CashFlow.Movimientos ──────────────────────────────────────────
        (cashflow["Movimientos"], [("fecha", -1)],
            "Movimientos: fecha"),

        # ── Opciones.DataHistorica (rollup diario) ────────────────────────
        (opciones["DataHistorica"], [("fecha", -1)],
            "DataHistorica: fecha"),
        (opciones["DataHistorica"], [("symbol", 1), ("fecha", -1)],
            "DataHistorica: symbol + fecha"),
    ]

    print(f"Creando {len(indices)} índices...\n")
    for entry in indices:
        col, keys, desc = entry[0], entry[1], entry[2]
        kwargs = entry[3] if len(entry) > 3 else {}
        try:
            col.create_index(keys, **kwargs)
            print(f"  OK       {desc}")
        except Exception as e:
            print(f"  SKIP     {desc}  ({e})")

    print("\nTodos los índices procesados.")


if __name__ == "__main__":
    main()
