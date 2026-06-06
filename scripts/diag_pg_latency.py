"""scripts/diag_pg_latency.py — ¿por qué SQL tarda? Mide el viaje de red a Postgres.

100% LECTURA. Separa el "peaje de red" (round-trip Droplet→Supabase) del costo real de las
queries. Si `SELECT 1` ya tarda cientos de ms, el problema es la DISTANCIA (región de Supabase
lejos del Droplet) → la solución es un proyecto Supabase en la región del Droplet (o un PG
co-locado), NO tunear queries.

Reporta: región del Droplet (DigitalOcean), host/región de Supabase, latencia de `SELECT 1`
(warm, el round-trip puro) y costo de abrir una conexión nueva.

    python -m scripts.diag_pg_latency
"""
from __future__ import annotations

import time
import urllib.request
from urllib.parse import urlparse

from core.postgres import connect, get_pool, get_postgres_uri


def _do_region() -> str:
    try:
        return urllib.request.urlopen(
            "http://169.254.169.254/metadata/v1/region", timeout=2
        ).read().decode().strip()
    except Exception as e:
        return f"(no se pudo leer metadata DO: {e})"


def _select1_ms(cur) -> float:
    t = time.perf_counter()
    cur.execute("SELECT 1")
    cur.fetchone()
    return (time.perf_counter() - t) * 1000


def main() -> int:
    host = urlparse(get_postgres_uri().replace("postgresql://", "http://")).hostname or "?"
    # host típico: aws-0-<region>.pooler.supabase.com → la región está en el medio.
    sb_region = "?"
    if "pooler.supabase.com" in host and host.startswith("aws-0-"):
        sb_region = host.split("aws-0-")[1].split(".pooler")[0]

    print(f"Droplet (DigitalOcean) región : {_do_region()}")
    print(f"Supabase host                 : {host}")
    print(f"Supabase región               : {sb_region}")

    # Costo de abrir una conexión NUEVA (handshake TLS + auth pooler).
    t = time.perf_counter()
    c = connect()
    c.close()
    print(f"\nAbrir conexión nueva          : {(time.perf_counter() - t) * 1000:8.1f} ms")

    # SELECT 1 sobre conexión del pool ya abierta = round-trip puro de red.
    with get_pool().connection() as conn, conn.cursor() as cur:
        _select1_ms(cur)  # warmup
        best = min(_select1_ms(cur) for _ in range(10))
    print(f"SELECT 1 (warm, round-trip)   : {best:8.1f} ms")

    print("\nLectura:")
    print("  • SELECT 1 < 10ms  → red OK, la lentitud sería de las queries (otra cosa).")
    print("  • SELECT 1 > 100ms → PEAJE DE RED: Supabase está lejos del Droplet.")
    print("    Fix: proyecto Supabase en la región del Droplet (o PG co-locado en DO).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
