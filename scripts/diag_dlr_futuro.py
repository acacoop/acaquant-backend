"""diag_dlr_futuro.py — RADIOGRAFÍA de los boletos de DÓLAR FUTURO (DLR).

One-shot READ-ONLY (REGLA #0/#2): antes de construir la vista NEGOCIO → DÓLAR
FUTURO hay que VERIFICAR cómo lucen los datos reales. La palabra clave es DLR y
el nocional sale de `cantidad` (la tabla NO tiene columna de precio). Este diag
responde:

  1. ¿Cómo se llaman los instrumentos DLR? (¿hay mini-contratos tipo 'MIN'?)
  2. ¿Qué tipo_operacion tienen? (Futuros Financieros - Compra/Venta, cierres…)
  3. ¿Qué pasa con es_cierre / etapa / moneda? (qué filas contar y cuáles no)
  4. Rango de `cantidad` (¿enteros = nº de contratos? ¿fraccionarios?)
  5. Relación bruto ↔ cantidad (¿bruto ya trae un nocional?)
  6. Top cuentas y serie mensual (nº de contratos + arancel)

Correr en el Droplet:  cd /root/TradingAV && git pull && python -m scripts.diag_dlr_futuro
"""
from __future__ import annotations

from api.services._sql import _q

T = "operaciones.operaciones"
DLR = "instrumento ILIKE '%%DLR%%'"


def _p(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    return _q(sql, params or {})


def main() -> None:
    # 0) ¿Cuántos boletos DLR hay en total?
    _p("0) TOTALES DLR (instrumento ILIKE '%DLR%')")
    for r in _rows(
        f"SELECT count(*) AS n, SUM(cantidad) AS cant, SUM(arancel) AS arancel, "
        f"SUM(bruto) AS bruto, MIN(concertacion) AS desde, MAX(concertacion) AS hasta "
        f"FROM {T} WHERE {DLR}"
    ):
        print(f"  boletos={r['n']}  Σcantidad={r['cant']}  Σarancel={r['arancel']}  "
              f"Σbruto={r['bruto']}  rango=[{r['desde']} → {r['hasta']}]")

    # 1) Nombres de instrumento DLR (¿mini-contratos? ¿variantes?)
    _p("1) INSTRUMENTOS DLR distintos (top 40 por nº de boletos)")
    print(f"  {'instrumento':<22}{'n':>8}{'Σcantidad':>16}{'Σarancel':>16}{'Σbruto':>18}")
    for r in _rows(
        f"SELECT instrumento, count(*) AS n, SUM(cantidad) AS cant, "
        f"SUM(arancel) AS ar, SUM(bruto) AS bruto FROM {T} WHERE {DLR} "
        f"GROUP BY instrumento ORDER BY n DESC LIMIT 40"
    ):
        print(f"  {str(r['instrumento']):<22}{r['n']:>8}{str(r['cant']):>16}"
              f"{str(r['ar']):>16}{str(r['bruto']):>18}")

    # 2) tipo_operacion de los DLR
    _p("2) tipo_operacion de los boletos DLR")
    print(f"  {'tipo_operacion':<48}{'n':>8}{'Σcantidad':>14}{'Σarancel':>14}")
    for r in _rows(
        f"SELECT tipo_operacion, count(*) AS n, SUM(cantidad) AS cant, "
        f"SUM(arancel) AS ar FROM {T} WHERE {DLR} "
        f"GROUP BY tipo_operacion ORDER BY n DESC"
    ):
        print(f"  {str(r['tipo_operacion']):<48}{r['n']:>8}{str(r['cant']):>14}{str(r['ar']):>14}")

    # 3) es_cierre / etapa / moneda / operacion
    for col in ("es_cierre", "etapa", "moneda", "operacion", "mercado"):
        _p(f"3) distribución de `{col}` en DLR")
        for r in _rows(
            f"SELECT {col} AS v, count(*) AS n, SUM(cantidad) AS cant, SUM(arancel) AS ar "
            f"FROM {T} WHERE {DLR} GROUP BY {col} ORDER BY n DESC"
        ):
            print(f"  {str(r['v']):<40}n={r['n']:>7}  Σcantidad={r['cant']}  Σarancel={r['ar']}")

    # 4) Rango de cantidad — ¿enteros (nº de contratos) o fraccionarios?
    _p("4) RANGO de cantidad (DLR) — ¿enteros = contratos?")
    for r in _rows(
        f"SELECT MIN(cantidad) AS mn, MAX(cantidad) AS mx, AVG(cantidad) AS avg, "
        f"SUM(CASE WHEN cantidad <> ROUND(cantidad) THEN 1 ELSE 0 END) AS fraccionarios, "
        f"count(*) AS n FROM {T} WHERE {DLR}"
    ):
        print(f"  min={r['mn']}  max={r['mx']}  avg={r['avg']}  "
              f"fraccionarios={r['fraccionarios']}/{r['n']}")

    # 5) Relación bruto ↔ cantidad (¿bruto ya es un nocional? ratio por boleto)
    _p("5) MUESTRA: bruto / cantidad por boleto (para ver el tamaño de contrato)")
    print(f"  {'instrumento':<20}{'cantidad':>12}{'bruto':>16}{'bruto/cant':>16}{'arancel':>12}")
    for r in _rows(
        f"SELECT instrumento, cantidad, bruto, arancel, "
        f"CASE WHEN cantidad <> 0 THEN bruto / cantidad END AS ratio "
        f"FROM {T} WHERE {DLR} AND cantidad <> 0 AND bruto IS NOT NULL "
        f"ORDER BY concertacion DESC LIMIT 20"
    ):
        print(f"  {str(r['instrumento']):<20}{str(r['cantidad']):>12}{str(r['bruto']):>16}"
              f"{str(r['ratio']):>16}{str(r['arancel']):>12}")

    # 6) MUESTRA cruda de boletos DLR recientes
    _p("6) MUESTRA cruda (15 boletos DLR más recientes)")
    for r in _rows(
        f"SELECT concertacion, denominacion, instrumento, tipo_operacion, operacion, "
        f"cantidad, arancel, bruto, mep, es_cierre, etapa, moneda "
        f"FROM {T} WHERE {DLR} ORDER BY concertacion DESC LIMIT 15"
    ):
        print(f"  {r['concertacion']} | {str(r['instrumento']):<14} | "
              f"cant={str(r['cantidad']):>10} | ar={str(r['arancel']):>10} | "
              f"bruto={str(r['bruto']):>14} | mep={r['mep']} | cierre={r['es_cierre']} | "
              f"etapa={r['etapa']} | {str(r['tipo_operacion'])[:28]} | {str(r['denominacion'])[:24]}")

    # 7) Top cuentas por nº de contratos (Σcantidad) y arancel
    _p("7) TOP 15 cuentas por Σcantidad (nº de contratos) DLR")
    print(f"  {'denominacion':<40}{'n':>7}{'Σcantidad':>14}{'Σarancel':>16}")
    for r in _rows(
        f"SELECT denominacion AS d, count(*) AS n, SUM(cantidad) AS cant, "
        f"SUM(arancel) AS ar FROM {T} WHERE {DLR} "
        f"GROUP BY denominacion ORDER BY cant DESC NULLS LAST LIMIT 15"
    ):
        print(f"  {str(r['d'])[:38]:<40}{r['n']:>7}{str(r['cant']):>14}{str(r['ar']):>16}")

    # 8) Serie mensual (nº de contratos + arancel)
    _p("8) SERIE MENSUAL DLR (Σcantidad + Σarancel + nº boletos)")
    print(f"  {'mes':<10}{'n':>8}{'Σcantidad':>16}{'Σarancel':>18}")
    for r in _rows(
        f"SELECT to_char(concertacion, 'YYYY-MM') AS mes, count(*) AS n, "
        f"SUM(cantidad) AS cant, SUM(arancel) AS ar FROM {T} WHERE {DLR} "
        f"GROUP BY mes ORDER BY mes"
    ):
        print(f"  {r['mes']:<10}{r['n']:>8}{str(r['cant']):>16}{str(r['ar']):>18}")

    # 9) Universo 'Futuros Financieros' — ¿hay futuros NO-DLR (otras monedas)?
    _p("9) tipo_operacion ILIKE 'Futuros Financieros' — instrumentos (¿solo DLR?)")
    print(f"  {'instrumento (prefijo)':<24}{'n':>8}{'Σcantidad':>16}")
    for r in _rows(
        f"SELECT split_part(regexp_replace(instrumento, '[0-9]', '', 'g'), ' ', 1) AS pref, "
        f"count(*) AS n, SUM(cantidad) AS cant FROM {T} "
        f"WHERE tipo_operacion ILIKE '%%Futuros Financieros%%' "
        f"GROUP BY pref ORDER BY n DESC LIMIT 25"
    ):
        print(f"  {str(r['pref']):<24}{r['n']:>8}{str(r['cant']):>16}")

    print("\n" + "=" * 78)
    print("Pegá esta salida en el chat y definimos la fórmula del nocional.")
    print("=" * 78)


if __name__ == "__main__":
    main()
