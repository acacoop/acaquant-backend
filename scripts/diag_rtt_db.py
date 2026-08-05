"""scripts/diag_rtt_db.py — mide el RTT Droplet → Postgres/Supabase (read-only).

Responde con números la pregunta "¿cuánto paga CADA query solo por el viaje?":

  1. RTT puro:      30 × `SELECT 1` con conexión ya abierta (pool caliente).
                    El MÍNIMO ≈ latencia de red pura (no hay query más barata).
  2. Conexión fría: cuánto cuesta abrir una conexión nueva (lo paga un worker
                    al arrancar o el pool al crecer).
  3. Queries reales: 3 lecturas representativas del hot path, cronometradas.
  4. Contexto del host: cores / carga — para dimensionar workers de uvicorn.

Interpretación (se imprime al final):
  - RTT mín < 5ms  → base y Droplet están "al lado" (misma región): no hay
    nada que ganar mudando; los ~100ms de los endpoints son queries + Python.
  - RTT mín 20-80ms → cada query paga ese peaje SIEMPRE. Un endpoint de 5
    queries en serie regala 5 × RTT. Acercar base y Droplet a la misma región
    sería la mejora más grande disponible en todo el sistema.

Uso (en el Droplet):
    python -m scripts.diag_rtt_db
"""
from __future__ import annotations

import os
import statistics
import time

from core.postgres import get_pool

_N_PINGS = 30


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def main() -> int:
    pool = get_pool()

    # ── 1. RTT puro (conexión caliente) ──
    tiempos: list[float] = []
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")  # warm-up (fuera de la muestra)
        for _ in range(_N_PINGS):
            t0 = time.perf_counter()
            cur.execute("SELECT 1")
            cur.fetchone()
            tiempos.append(_ms(t0))
    tiempos.sort()
    p50 = statistics.median(tiempos)
    p95 = tiempos[int(len(tiempos) * 0.95) - 1]
    print(f"1) RTT puro (SELECT 1 × {_N_PINGS}, pool caliente)")
    print(f"   min {tiempos[0]:6.1f} ms   p50 {p50:6.1f} ms   p95 {p95:6.1f} ms")

    # ── 2. Conexión fría (fuera del pool) ──
    import psycopg
    conninfo = os.environ.get("POSTGRES_URI", "")
    if conninfo:
        t0 = time.perf_counter()
        with psycopg.connect(conninfo) as c, c.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        print(f"2) Conexión NUEVA + SELECT 1: {_ms(t0):6.1f} ms "
              f"(lo paga cada worker al arrancar / el pool al crecer)")
    else:
        print("2) Conexión fría: POSTGRES_URI no está en el env — salteado")

    # ── 3. Queries reales del hot path ──
    reales = [
        ("market_snapshot (~300 filas, 6 cols)",
         "SELECT ticker, last_price, tea, tem, duration, paridad "
         "FROM mercado.market_snapshot"),
        ("curvas tasa_fija (flujo_vencimiento)",
         "SELECT ticker, flujo_vencimiento FROM mercado.curvas "
         "WHERE curva = 'tasa_fija'"),
        ("ops_agregado_diario (serie completa)",
         "SELECT fecha, moneda_calc, bruto, arancel "
         "FROM operaciones.ops_agregado_diario"),
    ]
    print("3) Queries reales (pool caliente, 2ª corrida de cada una):")
    with pool.connection() as conn, conn.cursor() as cur:
        for label, sql in reales:
            try:
                cur.execute(sql)          # 1ª: calienta caches de PG
                cur.fetchall()
                t0 = time.perf_counter()
                cur.execute(sql)
                n = len(cur.fetchall())
                print(f"   {_ms(t0):6.1f} ms  ({n:>5} filas)  {label}")
            except Exception as e:  # tabla ausente → seguir con el resto
                print(f"      ERR  {label}: {e}")

    # ── 4. Contexto del host ──
    try:
        la1, la5, _ = os.getloadavg()
        print(f"4) Host: {os.cpu_count()} cores · load 1m={la1:.2f} 5m={la5:.2f} "
              f"(load ≈ nº de cores ocupados; si 1m se acerca al nº de cores, hay cola)")
    except OSError:
        print(f"4) Host: {os.cpu_count()} cores")

    print()
    print("Lectura: si el RTT mín es <5ms, base y Droplet están en la misma región")
    print("y no hay peaje de red que recortar. Si es 20-80ms, CADA query del")
    print("sistema paga ese viaje — acercar base y API sería la mayor mejora")
    print("disponible (y explica por qué paralelizar/cachear rinde tanto).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
