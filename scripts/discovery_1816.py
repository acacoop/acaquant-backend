"""scripts/discovery_1816.py — DISCOVERY de la API de 1816 (datos de mercado RF).

Objetivo: pegarle a cada endpoint de la API de 1816, GUARDAR las responses crudas
y RESUMIR los shapes, para después modelar la integración en nuestro sistema
(probablemente `core/api1816.py`, al estilo de core/byma.py / core/finnhub.py).

── CONSCIENTE DE CRÉDITOS ──────────────────────────────────────────────────────
Cada request a endpoints de datos consume créditos (HTTP 402 si no alcanzan):
  • curvas / instrumentos → 1 c/u
  • indicadores (bulk)     → tickers × campos
  • indicadores/{ticker}   → campos
  • series                 → tickers × campos × días
El script ARRANCA por el balance (gratis), imprime el costo ESTIMADO del plan de
discovery y usa muestras chicas. Lo pesado (series largas, muchos campos) queda
detrás de --full. El balance solo se ve con --balance.

── Config (env var o flag; el flag pisa la env) ────────────────────────────────
  API_1816_BASE_URL / --base-url   base URL (ej. https://api.1816...) — REQUERIDO
  API_1816_KEY      / --api-key    API Key del usuario (webapp 1816)  — REQUERIDO
  API_1816_MODULE   / --module     módulo de la API Key               — REQUERIDO

── Uso ─────────────────────────────────────────────────────────────────────────
  python -m scripts.discovery_1816 --balance          # solo balance (gratis)
  python -m scripts.discovery_1816                     # discovery liviano (~22 cr)
  python -m scripts.discovery_1816 --full              # + series más largas
  python -m scripts.discovery_1816 --only curvas       # un endpoint puntual
  python -m scripts.discovery_1816 --tickers AL30,GD30 # cambia los tickers muestra

Las responses crudas se guardan en scripts/out_1816/<endpoint>.json (gitignored).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

BASE_DEFAULT = os.getenv("API_1816_BASE_URL", "")
KEY_DEFAULT = os.getenv("API_1816_KEY", "")
MODULE_DEFAULT = os.getenv("API_1816_MODULE", "")

OUT_DIR = Path(__file__).resolve().parent / "out_1816"
TIMEOUT = 30

# Muestras por defecto (chicas, para gastar poco).
CAMPOS_INDICADORES = ["ticker", "denominacion", "moneda", "precioClean", "precioDirty",
                      "tna", "tea", "paridad", "duration"]
CAMPOS_SERIES = ["precioClean", "tea", "paridad"]


# ── HTTP helpers ────────────────────────────────────────────────────────────────
def _guardar(nombre: str, obj: Any) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"{nombre}.json").write_text(
        json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _shape(obj: Any, prof: int = 0, max_prof: int = 4) -> str:
    """Descripción compacta del shape de un JSON (claves + tipos + muestra escalar)."""
    ind = "  " * prof
    if isinstance(obj, dict):
        if prof >= max_prof:
            return "{…}"
        lineas = []
        for k, v in list(obj.items())[:40]:
            lineas.append(f"{ind}  {k}: {_shape(v, prof + 1, max_prof)}")
        return "{\n" + "\n".join(lineas) + f"\n{ind}}}"
    if isinstance(obj, list):
        if not obj:
            return "[] (vacío)"
        return f"[{len(obj)}× " + _shape(obj[0], prof, max_prof) + "]"
    if isinstance(obj, str):
        return f"str ({obj[:24]!r})" if obj else "str ('')"
    if isinstance(obj, bool):
        return f"bool ({obj})"
    if isinstance(obj, (int, float)):
        return f"num ({obj})"
    if obj is None:
        return "null"
    return type(obj).__name__


def _req(sess: requests.Session, base: str, path: str, token: str | None,
         params: dict | None, nombre: str) -> Any | None:
    """GET a un endpoint de datos. Guarda + resume la response. None si falló."""
    url = base.rstrip("/") + path
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = sess.get(url, headers=headers, params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"  ✗ {nombre}: error de red — {e}")
        return None

    print(f"  → GET {path}  [{r.status_code}]  params={params or {}}")
    try:
        body = r.json()
    except ValueError:
        print(f"  ✗ {nombre}: response no-JSON:\n{r.text[:400]}")
        return None

    _guardar(nombre, body)
    if r.status_code != 200:
        # 400/401/402/403/429 traen {error, message}
        print(f"  ✗ {nombre}: {body}")
        return None
    print(f"  ✓ {nombre}: shape →\n{_shape(body)}")
    return body


# ── Auth ─────────────────────────────────────────────────────────────────────────
def obtener_token(sess: requests.Session, base: str, api_key: str, module: str) -> str | None:
    url = base.rstrip("/") + "/v1/auth/token"
    print(f"AUTH  POST /v1/auth/token  (module={module})")
    try:
        r = sess.post(url, json={"apiKey": api_key, "module": module}, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"  ✗ auth: error de red — {e}")
        return None
    try:
        body = r.json()
    except ValueError:
        print(f"  ✗ auth: response no-JSON [{r.status_code}]:\n{r.text[:400]}")
        return None
    if r.status_code != 200:
        print(f"  ✗ auth [{r.status_code}]: {body}")
        return None
    _guardar("auth_token", body)
    exp = body.get("expiresIn")
    print(f"  ✓ token OK (module={body.get('module')}, expiresIn={exp}s)")
    return body.get("token")


# ── Discovery por endpoint ───────────────────────────────────────────────────────
def disc_balance(sess, base, token) -> None:
    print("\n[1] BALANCE (gratis)")
    _req(sess, base, "/v1/creditos/balance", token, None, "creditos_balance")


def disc_curvas(sess, base, token) -> list[dict]:
    print("\n[2] CURVAS (1 crédito) — texto='soberano'")
    body = _req(sess, base, "/v1/mercado/curvas", token, {"texto": "soberano"}, "curvas_soberano")
    return body if isinstance(body, list) else []


def disc_instrumentos(sess, base, token, curva_id: int | None) -> list[dict]:
    print("\n[3] INSTRUMENTOS (1 crédito) — texto='AL30'")
    body = _req(sess, base, "/v1/mercado/instrumentos", token, {"texto": "AL30"}, "instrumentos_texto")
    if curva_id is not None:
        print(f"\n[3b] INSTRUMENTOS por curvaId={curva_id} (1 crédito)")
        _req(sess, base, "/v1/mercado/instrumentos", token, {"curvaId": curva_id},
             "instrumentos_curva")
    return body if isinstance(body, list) else []


def disc_indicadores(sess, base, token, tickers: list[str]) -> None:
    campos = CAMPOS_INDICADORES
    costo = len(tickers) * len(campos)
    print(f"\n[4] INDICADORES bulk ({len(tickers)}×{len(campos)} = {costo} créditos)")
    _req(sess, base, "/v1/mercado/indicadores", token,
         {"tickers": tickers, "campos": campos}, "indicadores_bulk")

    print(f"\n[5] INDICADORES manual /{tickers[0]} (input precioClean, {len(campos)} créditos)")
    campos_manual = [c for c in campos if c != "denominacion"]  # el ejemplo del doc
    _req(sess, base, f"/v1/mercado/indicadores/{tickers[0]}", token,
         {"campos": campos_manual, "precioClean": 72.5}, "indicadores_manual")


def disc_series(sess, base, token, tickers: list[str], dias: int) -> None:
    campos = CAMPOS_SERIES
    hoy = datetime.now(UTC).date()
    ini = hoy - timedelta(days=dias)
    costo = len(tickers) * len(campos) * dias
    print(f"\n[6] SERIES ({len(tickers)}×{len(campos)}×{dias}d ≈ {costo} créditos) "
          f"[{ini} → {hoy}]")
    _req(sess, base, "/v1/mercado/series", token,
         {"tickers": tickers, "campos": campos,
          "fechaInicial": ini.isoformat(), "fechaFinal": hoy.isoformat()},
         "series")


# ── main ─────────────────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="Discovery de la API de 1816.")
    p.add_argument("--base-url", default=BASE_DEFAULT)
    p.add_argument("--api-key", default=KEY_DEFAULT)
    p.add_argument("--module", default=MODULE_DEFAULT)
    p.add_argument("--tickers", default="AL30,GD30", help="CSV de tickers muestra")
    p.add_argument("--only", choices=["balance", "curvas", "instrumentos", "indicadores",
                                      "series"], help="Correr solo un endpoint")
    p.add_argument("--balance", action="store_true", help="Solo balance (gratis)")
    p.add_argument("--full", action="store_true", help="Series más largas (más créditos)")
    args = p.parse_args()

    faltan = [n for n, v in (("--base-url/API_1816_BASE_URL", args.base_url),
                             ("--api-key/API_1816_KEY", args.api_key),
                             ("--module/API_1816_MODULE", args.module)) if not v]
    if faltan:
        print("FALTA config: " + ", ".join(faltan))
        print("Pasala por env (API_1816_*) o por flag. Ver el docstring del script.")
        return 2

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    dias = 30 if args.full else 5

    sess = requests.Session()
    print(f"BASE = {args.base_url}   MODULE = {args.module}")
    token = obtener_token(sess, args.base_url, args.api_key, args.module)
    if not token:
        return 1

    if args.balance or args.only == "balance":
        disc_balance(sess, args.base_url, token)
        return 0

    # Siempre el balance primero (gratis) para ver el presupuesto.
    disc_balance(sess, args.base_url, token)

    curva_id = None
    if args.only in (None, "curvas", "instrumentos"):
        curvas = disc_curvas(sess, args.base_url, token)
        if curvas and isinstance(curvas[0], dict):
            curva_id = curvas[0].get("id")

    if args.only in (None, "instrumentos"):
        disc_instrumentos(sess, args.base_url, token, curva_id)

    if args.only in (None, "indicadores"):
        disc_indicadores(sess, args.base_url, token, tickers)

    if args.only in (None, "series"):
        disc_series(sess, args.base_url, token, tickers, dias)

    print(f"\n✓ Discovery listo. Responses crudas en: {OUT_DIR}")
    print("  Revisá los .json para modelar. Balance final:")
    disc_balance(sess, args.base_url, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
