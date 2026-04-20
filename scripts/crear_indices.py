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

    trading         = client["Trading"]
    valuaciones     = client["Valuaciones"]
    cashflow        = client["CashFlow"]
    opciones        = client["Opciones"]
    cuentas_api     = client["CuentasAPI"]
    operaciones_api = client["OperacionesAPI"]
    portfolio_api   = client["PortfolioAPI"]
    titulos_api     = client["TitulosAPI"]
    manager_db      = client["Manager"]

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
        (valuaciones["AuM"], [("cuenta", 1), ("fecha_snapshot", -1)],
            "AuM: cuenta + fecha_snapshot"),

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

        # ── Valuaciones.Dolar ─────────────────────────────────────────────
        (valuaciones["Dolar"], [("timestamp", -1)],
            "Dolar: timestamp"),

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

        # ── CuentasAPI (colecciones derivadas consumidas por la API REST) ─
        (cuentas_api["ContrapartesAPI"], [("grupo", 1)],
            "ContrapartesAPI: grupo"),

        # ── OperacionesAPI ────────────────────────────────────────────────
        (operaciones_api["MesaAPI"], [("contraparte", 1), ("moneda", 1)],
            "MesaAPI: contraparte + moneda"),
        (operaciones_api["MesaAPI"], [("concertacion", -1)],
            "MesaAPI: concertacion"),

        # ── PortfolioAPI ──────────────────────────────────────────────────
        (portfolio_api["AumAPI"], [("fecha", -1)],
            "AumAPI: fecha"),
        (portfolio_api["AumAPI"], [("unidad", 1), ("fecha", -1)],
            "AumAPI: unidad + fecha"),
        (portfolio_api["CarterasAPI"], [("id_cuenta", 1), ("unidad", 1)],
            "CarterasAPI: id_cuenta + unidad"),
        (portfolio_api["CarterasAPI"], [("unidad", 1)],
            "CarterasAPI: unidad (queries sin id_cuenta)"),

        # ── TitulosAPI ────────────────────────────────────────────────────
        (titulos_api["AssetsAPI"], [("cartera", 1)],
            "AssetsAPI: cartera"),
        (titulos_api["AssetsAPI"], [("emisor", 1), ("cartera", 1)],
            "AssetsAPI: emisor + cartera"),
        (titulos_api["ValuacionesAPI"], [("curva", 1)],
            "ValuacionesAPI: curva"),
        (titulos_api["ValuacionesAPI"], [("ticker", 1)],
            "ValuacionesAPI: ticker"),

        # ── Manager.JobRuns (historial de runs de cron) ──────────────────
        # TTL: expira 60 días después de started_at.
        (manager_db["JobRuns"], [("started_at", -1)],
            "JobRuns: started_at (TTL 60d)",
            {"expireAfterSeconds": 60 * 24 * 3600}),
        (manager_db["JobRuns"], [("tipo", 1), ("started_at", -1)],
            "JobRuns: tipo + started_at"),
        (manager_db["JobRuns"], [("status", 1), ("started_at", -1)],
            "JobRuns: status + started_at"),
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
