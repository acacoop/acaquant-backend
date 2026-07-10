"""scripts/diag_briefing_datos.py — ¿qué datos tendría el Briefing de apertura AHORA?

Diagnóstico READ-ONLY para el diseño del briefing de las 10:00 ART (QuantAI P1).
Imprime, con su frescura, cada dato que el cartel mostraría si se generara en
este momento:

  1. Futuros S&P (ES=F) y NASDAQ (NQ=F)   ← home.market_quotes (Yahoo, cron 1min)
  2. Dólar oficial mayorista LIVE (MAE)   ← valuaciones.dolar_oficial_live
  3. A3500 fixing (BCRA, cierres)         ← macro.series_macro clave DOLAR
  4. MEP / CCL cierre por día (últ. 8)    ← valuaciones.dolar (último valor del día)
  5. MEP / CCL snapshot live               ← valuaciones.dolar_snapshot (si hay rueda)

Correrlo idealmente ~10:00 ART (hora a la que saldría el briefing):
    python -m scripts.diag_briefing_datos
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from core.postgres import connect


def _hace(ts) -> str:
    if ts is None:
        return "sin timestamp"
    delta = datetime.now(UTC) - ts
    m = int(delta.total_seconds() // 60)
    return f"hace {m} min" if m < 120 else f"hace {m // 60} h {m % 60} min"


def main() -> None:
    print(f"AHORA (UTC): {datetime.now(UTC).isoformat()}\n")
    with connect() as conn, conn.cursor() as cur:
        # 1) Futuros de índices
        print("── 1. FUTUROS (home.market_quotes) ──────────────────────────")
        # OJO: la tabla es (symbol, grupo, data jsonb) y el writer usa el LABEL
        # como PK ('S&P FUT'), no el símbolo Yahoo — se matchea por ambos.
        cur.execute(
            "SELECT symbol, data FROM home.market_quotes"
            " WHERE symbol IN ('S&P FUT', 'NASDAQ FUT')"
            "    OR data->>'yahoo_sym' IN ('ES=F', 'NQ=F') ORDER BY symbol"
        )
        filas = cur.fetchall()
        if not filas:
            print("  ✗ SIN FILAS para ES=F / NQ=F")
        for sym, data in filas:
            print(f"  {sym}:")
            print(f"    {json.dumps(data, ensure_ascii=False, default=str)[:700]}")

        # 2) Oficial mayorista live (MAE)
        print("\n── 2. OFICIAL MAYORISTA LIVE (valuaciones.dolar_oficial_live) ──")
        cur.execute(
            "SELECT data, updated_at FROM valuaciones.dolar_oficial_live"
            " WHERE ticker = 'UST$T' AND codigo_segmento = 'M' AND codigo_plazo = '000'"
        )
        fila = cur.fetchone()
        if not fila:
            print("  ✗ SIN FILA mayorista M/000 (¿script de la PC de oficina corriendo?)")
        else:
            data, upd = fila
            print(f"  frescura: {_hace(upd)}")
            print(f"  {json.dumps(data, ensure_ascii=False, default=str)[:500]}")

        # 3) A3500 (fixing BCRA — cierres oficiales)
        print("\n── 3. A3500 (macro.series_macro clave DOLAR, últimos 3) ─────")
        cur.execute(
            "SELECT fecha, valor FROM macro.series_macro"
            " WHERE serie = 'DOLAR' ORDER BY fecha DESC LIMIT 3"
        )
        filas = cur.fetchall()
        if not filas:
            print("  ✗ SIN DATOS en series_macro DOLAR")
        for fecha, valor in filas:
            print(f"  {fecha}  {valor}")

        # 4) MEP / CCL — último valor por día (cierre implícito), últimos 8 días
        print("\n── 4. MEP/CCL cierre por día (valuaciones.dolar, últ. 8 días) ──")
        cur.execute(
            """
            SELECT DISTINCT ON (date(timestamp)) date(timestamp) AS dia,
                   mep, ccl, timestamp
            FROM valuaciones.dolar
            WHERE mep IS NOT NULL
              AND timestamp >= now() - interval '14 days'
            ORDER BY date(timestamp) DESC, timestamp DESC
            LIMIT 8
            """
        )
        filas = cur.fetchall()
        if not filas:
            print("  ✗ SIN DATOS recientes en valuaciones.dolar")
        prev = None
        for dia, mep, ccl, ts in filas:
            var = ""
            if prev and mep and prev[1]:
                var = f"  (mep vs día post.: {round((float(prev[1]) / float(mep) - 1) * 100, 2)}%)"
            print(f"  {dia}  mep={mep}  ccl={ccl}  último trade {ts:%H:%M}Z{var}")
            prev = (dia, mep)

        # 5) Snapshot live MEP/CCL (solo tiene sentido en horario de rueda)
        print("\n── 5. MEP/CCL live (valuaciones.dolar_snapshot) ─────────────")
        cur.execute("SELECT ts, mep, ccl, canje FROM valuaciones.dolar_snapshot WHERE id = 'current'")
        fila = cur.fetchone()
        if not fila:
            print("  (sin snapshot — normal fuera de rueda)")
        else:
            ts, mep, ccl, canje = fila
            print(f"  {_hace(ts)}: mep={mep} ccl={ccl} canje={canje}")

    print("\nListo. Pasale este output a Claude para diseñar el briefing con datos reales.")


if __name__ == "__main__":
    main()
