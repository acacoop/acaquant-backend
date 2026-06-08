"""scripts/diag_retorno_soberanos.py — por qué "Hard Dólar" (curva=soberanos)
viene SIN DATOS en la vista Retorno Total de la home.

La home (retorno-total-mini) pinta una línea por bono SOLO si tiene >= 2 puntos
de precio en la ventana. La data de cada curva sale de:
  - Trading.SnapshotsCierre  (1 doc por (curva, fecha, ticker)) — el histórico.
  - Trading.MarketSnapshot   — fila de HOY si todavía no corrió el cron de cierre.
  - Trading.Curvas           — el universo de tickers de la curva (+ flujos).

Si para curva="soberanos" no hay histórico en SnapshotsCierre (o solo hay 1 fecha),
cada ticker queda con <2 puntos → el frontend lo descarta → "sin datos".

Este diag mide, read-only, dónde se corta la cadena para soberanos vs las curvas
que SÍ andan (tasa_fija, cer):

  1. Trading.Curvas       → qué valores de `curva` existen y cuántos tickers tiene
                            cada uno (¿está cargado "soberanos"?).
  2. Trading.SnapshotsCierre → por curva: nº docs, nº tickers, nº fechas distintas,
                            rango de fechas (¿hay histórico de soberanos?).
  3. Trading.MarketSnapshot → de los tickers soberanos, cuántos tienen last_price>0
                            (¿al menos hay fila de hoy?).

Read-only. Correr en el Droplet:
    python -m scripts.diag_retorno_soberanos
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read

CURVAS_VISTA = ["tasa_fija", "cer", "soberanos"]


def main() -> None:
    cli = get_mongo_client_read()
    trd = cli["Trading"]

    print("=" * 70)
    print("1) Trading.Curvas — universo de tickers por valor de `curva`")
    print("=" * 70)
    curvas_existentes = sorted(trd["Curvas"].distinct("curva"))
    print(f"Valores distintos de `curva`: {curvas_existentes}\n")
    for c in curvas_existentes:
        n = trd["Curvas"].count_documents({"curva": c})
        tickers = trd["Curvas"].distinct("ticker_corto", {"curva": c})
        marca = "  <-- VISTA" if c in CURVAS_VISTA else ""
        print(f"  curva={c!r:16} docs={n:4}  tickers={len(tickers)}{marca}")
        if c == "soberanos":
            print(f"      tickers soberanos: {sorted(t for t in tickers if t)}")

    print("\n" + "=" * 70)
    print("2) Trading.SnapshotsCierre — histórico por curva")
    print("=" * 70)
    sc_curvas = sorted(trd["SnapshotsCierre"].distinct("curva"))
    print(f"Valores distintos de `curva` en SnapshotsCierre: {sc_curvas}\n")
    for c in CURVAS_VISTA:
        n = trd["SnapshotsCierre"].count_documents({"curva": c})
        if n == 0:
            print(f"  curva={c!r:16} SIN DOCS en SnapshotsCierre  <-- aquí se corta")
            continue
        tickers = trd["SnapshotsCierre"].distinct("ticker_corto", {"curva": c})
        fechas = sorted(f for f in trd["SnapshotsCierre"].distinct("ts_cierre", {"curva": c}) if f)
        rango = f"{fechas[0]} → {fechas[-1]}" if fechas else "—"
        print(f"  curva={c!r:16} docs={n:5}  tickers={len(tickers):3}  "
              f"fechas_distintas={len(fechas):3}  rango=[{rango}]")
        if len(fechas) < 2:
            print("      ⚠ menos de 2 fechas → el frontend descarta TODO (necesita >=2 puntos)")

    print("\n" + "=" * 70)
    print("3) Trading.MarketSnapshot — fila de HOY para tickers soberanos")
    print("=" * 70)
    sob_tickers_full = trd["Curvas"].distinct("ticker", {"curva": "soberanos"})
    if not sob_tickers_full:
        print("  No hay tickers con curva='soberanos' en Trading.Curvas → nada que buscar.")
    else:
        con_precio = trd["MarketSnapshot"].count_documents(
            {"ticker": {"$in": sob_tickers_full}, "metrics.last_price": {"$gt": 0}}
        )
        print(f"  tickers soberanos en Curvas: {len(sob_tickers_full)}")
        print(f"  con last_price>0 en MarketSnapshot (fila de hoy): {con_precio}")
        muestra = list(trd["MarketSnapshot"].find(
            {"ticker": {"$in": sob_tickers_full}},
            {"_id": 0, "ticker": 1, "metrics.last_price": 1},
        ).limit(8))
        for m in muestra:
            lp = (m.get("metrics") or {}).get("last_price")
            print(f"      {m.get('ticker'):28} last_price={lp}")

    print("\n" + "=" * 70)
    print("DIAGNÓSTICO")
    print("=" * 70)
    print("- Si (1) no lista 'soberanos' → no hay bonos cargados en Trading.Curvas.")
    print("- Si (2) soberanos tiene 0 docs o <2 fechas → falta histórico de cierre")
    print("  (cada ticker queda con <2 puntos y el frontend lo descarta).")
    print("- Si (3) tiene last_price>0 pero (2) está vacío → solo hay punto de HOY.")


if __name__ == "__main__":
    main()
