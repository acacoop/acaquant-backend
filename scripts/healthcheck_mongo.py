"""healthcheck_mongo.py — ping rápido a las 3 conexiones Mongo del proyecto.

Diseñado para correr DESPUÉS de tocar la whitelist de Atlas (Network Access).
Si alguna conexión falla por whitelist, lo detectás acá en 5 segundos en
vez de descubrirlo cuando la mesa se queja del 502.

Chequea:
  1. MONGO_URI         — cliente RW (motores/jobs/API write paths).
  2. MONGO_URI_READ    — cliente RO secondaryPreferred (API queries).
  3. PARTNER_MONGO_URI — partner_api (RO sobre ACAPortfolio).

Cada uno: `client.admin.command('ping')` con timeout corto. Imprime OK/FAIL.

Uso:
    python -m scripts.healthcheck_mongo
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import pymongo
from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


VARS = ("MONGO_URI", "MONGO_URI_READ", "PARTNER_MONGO_URI")
TIMEOUT_MS = 5000


def ping(var: str) -> None:
    uri = os.getenv(var)
    if not uri:
        print(f"  {var:<22s}  ⊘ (no seteada)")
        return
    user = urlparse(uri).username or "?"
    t0 = time.perf_counter()
    try:
        c = pymongo.MongoClient(uri, serverSelectionTimeoutMS=TIMEOUT_MS)
        c.admin.command("ping")
        dt = (time.perf_counter() - t0) * 1000
        print(f"  {var:<22s}  ✓ OK   user={user}   {dt:.0f} ms")
        c.close()
    except Exception as e:  # noqa: BLE001
        dt = (time.perf_counter() - t0) * 1000
        # Truncamos el error largo (Atlas tira mucho ruido).
        msg = str(e).splitlines()[0][:140]
        print(f"  {var:<22s}  ✗ FAIL user={user}   {dt:.0f} ms   {msg}")


def main() -> None:
    print(f"Healthcheck Mongo desde host actual (timeout {TIMEOUT_MS} ms):\n")
    for v in VARS:
        ping(v)
    print()


if __name__ == "__main__":
    main()
