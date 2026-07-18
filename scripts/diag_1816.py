"""scripts/diag_1816.py — explora TODOS los endpoints de la API de Mercado de 1816.

Diag read-only (doc funcional en docs/VISTA_RESEARCH.md §4): autentica y pega a
cada endpoint, imprimiendo status HTTP, créditos consumidos (header x-1816-credits)
y la respuesta (recortada). Sirve para ver QUÉ devuelve cada uno y decidir qué
modelar, antes de escribir el cliente/tablas de verdad.

Consume MUY pocos créditos (curvas/instrumentos = 1 c/u; indicadores/series con
pocos tickers×campos×días). Cada llamada imprime lo que gastó.

Requiere la API key (se genera en la webapp de 1816, módulo `mercado`):
  - env `MERCADO_1816_API_KEY` (en el .env del Droplet) o el arg --api-key.
La base URL NO está en el doc funcional: default `https://api.1816.com.ar`; si da
error de conexión/404, pasá la correcta con --base-url.

Uso:
    python -m scripts.diag_1816
    python -m scripts.diag_1816 --api-key XXX --base-url https://api.1816.com.ar
    python -m scripts.diag_1816 --solo series      # correr un solo bloque
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta

import requests
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))

_MAX_PRINT = 1800  # techo de caracteres por respuesta impresa


def _dump(resp: requests.Response) -> None:
    creditos = resp.headers.get("x-1816-credits", "—")
    print(f"  → HTTP {resp.status_code} · créditos: {creditos}")
    try:
        body = json.dumps(resp.json(), ensure_ascii=False, indent=2)
    except ValueError:
        body = resp.text
    if len(body) > _MAX_PRINT:
        body = body[:_MAX_PRINT] + f"\n  … (recortado, {len(body)} chars totales)"
    print("\n".join("  " + ln for ln in body.splitlines()))


def _seccion(titulo: str) -> None:
    print(f"\n{'=' * 74}\n{titulo}\n{'-' * 74}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", default=os.getenv("MERCADO_1816_API_KEY"))
    ap.add_argument("--base-url",
                    default=os.getenv("MERCADO_1816_BASE_URL", "https://api.1816.com.ar"))
    ap.add_argument("--solo", choices=["balance", "curvas", "instrumentos",
                                       "indicadores", "teorico", "series"],
                    help="correr solo ese bloque (default: todos)")
    args = ap.parse_args()

    if not args.api_key:
        print("✗ Falta la API key: seteá MERCADO_1816_API_KEY en el .env o pasá --api-key")
        return
    base = args.base_url.rstrip("/")
    print(f"Base URL: {base}")

    # ── 1) AUTH ──────────────────────────────────────────────────────────────
    _seccion("1) POST /v1/auth/token")
    try:
        r = requests.post(f"{base}/v1/auth/token",
                          json={"apiKey": args.api_key, "module": "mercado"}, timeout=30)
    except requests.RequestException as e:
        print(f"✗ No pude conectar a {base} ({e}). ¿La base URL es correcta? (--base-url)")
        return
    _dump(r)
    if r.status_code != 200:
        print("✗ Auth falló — no sigo (revisá api key / base url).")
        return
    token = r.json().get("token")
    if not token:
        print("✗ La respuesta de auth no trajo `token`.")
        return
    h = {"Authorization": f"Bearer {token}"}

    def get(path: str, params: dict | None = None) -> None:
        try:
            _dump(requests.get(f"{base}{path}", headers=h, params=params, timeout=45))
        except requests.RequestException as e:
            print(f"  ✗ error de red: {e}")

    corre = lambda b: args.solo is None or args.solo == b  # noqa: E731

    # ── 2) CRÉDITOS ──────────────────────────────────────────────────────────
    if corre("balance"):
        _seccion("2) GET /v1/creditos/balance")
        get("/v1/creditos/balance")

    # ── 3) CURVAS ────────────────────────────────────────────────────────────
    if corre("curvas"):
        _seccion("3) GET /v1/mercado/curvas (todas)")
        get("/v1/mercado/curvas")
        _seccion("3b) GET /v1/mercado/curvas?texto=soberano")
        get("/v1/mercado/curvas", {"texto": "soberano"})

    # ── 4) INSTRUMENTOS ──────────────────────────────────────────────────────
    if corre("instrumentos"):
        _seccion("4) GET /v1/mercado/instrumentos?texto=AL30")
        get("/v1/mercado/instrumentos", {"texto": "AL30"})

    campos = ["ticker", "precioDirty", "precioClean", "paridad", "tna", "tea",
              "duration", "durationMod", "currentYield", "ultimaOperacion"]

    # ── 5) INDICADORES (a precio de mercado) ─────────────────────────────────
    if corre("indicadores"):
        _seccion("5) GET /v1/mercado/indicadores?tickers=AL30,GD30&campos=…")
        get("/v1/mercado/indicadores", {"tickers": ["AL30", "GD30"], "campos": campos})

    # ── 6) INDICADORES teórico (input manual) ────────────────────────────────
    if corre("teorico"):
        _seccion("6) GET /v1/mercado/indicadores/AL30?precioDirty=90000&campos=…")
        get("/v1/mercado/indicadores/AL30",
            {"campos": ["tna", "tea", "paridad", "duration"], "precioDirty": 90000})

    # ── 7) SERIES HISTÓRICAS (la estrella) ───────────────────────────────────
    if corre("series"):
        hoy = datetime.now(UTC).date()
        _seccion("7) GET /v1/mercado/series?tickers=AL30&campos=…&30 días")
        get("/v1/mercado/series", {
            "tickers": ["AL30"],
            "campos": ["precioDirty", "tna", "tea", "paridad", "duration"],
            "fechaInicial": (hoy - timedelta(days=30)).isoformat(),
            "fechaFinal": hoy.isoformat(),
        })

    # ── saldo final ──────────────────────────────────────────────────────────
    _seccion("Balance final de créditos (cuánto gastó esta corrida)")
    get("/v1/creditos/balance")
    print(f"\n{'=' * 74}\nListo. Pegame la salida y vemos qué modelar.")


if __name__ == "__main__":
    main()
