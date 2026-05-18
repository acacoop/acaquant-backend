"""partner_api_smoke.py — prueba la Partner API de punta a punta.

Simula al proveedor externo conectándose por primera vez:
  1. GET  /health        — liveness, sin auth
  2. POST /v1/token      — login usuario/password → JWT
  3. GET  /v1/fechas     — fechas disponibles (con token)
  4. GET  /v1/portfolio  — posiciones (con token)

Se puede correr desde cualquier máquina con internet — no necesita estar
en el Droplet.

Uso:
  python -m scripts.partner_api_smoke --user <u> --password <p>
  python -m scripts.partner_api_smoke --user <u> --password <p> --cuenta 101
  python -m scripts.partner_api_smoke --user <u> --password <p> --base https://data.acaquant.com

Si no se pasan por CLI, toma el usuario/password de las env vars
PARTNER_SMOKE_USER / PARTNER_SMOKE_PASS.
"""
from __future__ import annotations

import argparse
import os
import sys

import requests

_DEFAULT_BASE = "https://data.acaquant.com"


def main() -> None:
    ap = argparse.ArgumentParser(description="Smoke test de la Partner API.")
    ap.add_argument("--base", default=_DEFAULT_BASE,
                    help=f"Base URL (default {_DEFAULT_BASE})")
    ap.add_argument("--user", default=os.getenv("PARTNER_SMOKE_USER", ""),
                    help="usuario del proveedor")
    ap.add_argument("--password", default=os.getenv("PARTNER_SMOKE_PASS", ""),
                    help="password del proveedor")
    ap.add_argument("--fecha", help="YYYY-MM-DD (default: la más reciente)")
    ap.add_argument("--cuenta", help="filtrar por un id_cuenta puntual")
    args = ap.parse_args()

    if not args.user or not args.password:
        print("Falta --user / --password (o las env PARTNER_SMOKE_USER/PASS).")
        sys.exit(1)

    base = args.base.rstrip("/")
    print(f"== Partner API smoke — {base} ==\n")

    # 1. Health — sin auth.
    print("[1] GET /health")
    r = requests.get(f"{base}/health", timeout=15)
    print(f"    {r.status_code}  {r.text}\n")
    r.raise_for_status()

    # 2. Login → token.
    print("[2] POST /v1/token")
    r = requests.post(
        f"{base}/v1/token",
        data={"username": args.user, "password": args.password},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"    ❌ {r.status_code}  {r.text}")
        sys.exit(1)
    tok = r.json()
    print(f"    ✅ token OK — expira en {tok.get('expires_in')}s\n")
    headers = {"Authorization": f"Bearer {tok['access_token']}"}

    # 3. Fechas disponibles.
    print("[3] GET /v1/fechas")
    r = requests.get(f"{base}/v1/fechas", headers=headers, timeout=30)
    print(f"    {r.status_code}  {r.text}\n")
    r.raise_for_status()

    # 4. Portfolio.
    params: dict[str, str] = {}
    if args.fecha:
        params["fecha"] = args.fecha
    if args.cuenta:
        params["id_cuenta"] = args.cuenta
    print(f"[4] GET /v1/portfolio  params={params or '{}'}")
    r = requests.get(f"{base}/v1/portfolio", headers=headers, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    posiciones = data.get("posiciones", [])
    print(f"    {r.status_code}  fecha={data.get('fecha')}  n={data.get('n')}")
    for p in posiciones[:10]:
        print(f"      {p}")
    if len(posiciones) > 10:
        print(f"      ... (+{len(posiciones) - 10} posiciones más)")

    print("\n✅ Smoke OK — la API responde de punta a punta.")


if __name__ == "__main__":
    main()
