"""Diagnóstico rápido del endpoint /api/titulos/flujos.

Compara lo que hay en Mongo (TitulosAPI.ValuacionesAPI) con lo que
devuelve el API local. Sirve para distinguir problemas de backend
(Mongo vacío, migrate que no corrió) de problemas de frontend
(ISR cache, proxy Next.js stale).

Uso:
    python -m scripts.check_api_flujos
"""
from __future__ import annotations

import os
from collections import defaultdict

import requests

from core.mongo import get_mongo_client


def main() -> int:
    client = get_mongo_client()
    col = client["TitulosAPI"]["ValuacionesAPI"]

    # ── Mongo directo ─────────────────────────────────────────────
    total = col.count_documents({})
    por_curva: dict[str, list[str]] = defaultdict(list)
    for d in col.find({}, {"_id": 0, "ticker": 1, "curva": 1}):
        cv = d.get("curva") or "(vacio)"
        por_curva[cv].append(d.get("ticker") or "?")

    print("=" * 60)
    print(f"MONGO · TitulosAPI.ValuacionesAPI")
    print("=" * 60)
    print(f"Total docs: {total}")
    for cv in sorted(por_curva.keys()):
        ts = por_curva[cv]
        muestra = ", ".join(ts[:5]) + (f" ... (+{len(ts)-5} mas)" if len(ts) > 5 else "")
        print(f"  {cv:<15} {len(ts):>4} tickers   [{muestra}]")

    # ── Endpoint local /api/titulos/flujos ────────────────────────
    api_key = ""
    try:
        with open("/root/TradingAV/.env") as f:
            for line in f:
                if line.strip().startswith("API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except OSError:
        pass

    if not api_key:
        api_key = os.environ.get("API_KEY", "")

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    print()
    print("=" * 60)
    print(f"HTTP · GET http://127.0.0.1:8000/api/titulos/flujos")
    print("=" * 60)
    try:
        r = requests.get(
            "http://127.0.0.1:8000/api/titulos/flujos",
            headers=headers,
            timeout=10,
        )
        print(f"status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"docs devueltos: {len(data) if isinstance(data, list) else 'no-lista'}")
            if isinstance(data, list) and data:
                por_curva_api: dict[str, int] = defaultdict(int)
                for d in data:
                    por_curva_api[d.get("curva") or "(vacio)"] += 1
                for cv, n in sorted(por_curva_api.items()):
                    print(f"  {cv:<15} {n:>4} tickers")
        else:
            print(f"body (primeros 300 chars): {r.text[:300]}")
    except Exception as e:
        print(f"ERROR: {e}")

    # ── Comparación y diagnóstico ──────────────────────────────────
    print()
    print("=" * 60)
    print("DIAGNOSTICO")
    print("=" * 60)
    if total == 0:
        print("✗ Mongo vacío. Corré: python -m scripts.api_migrate flujos-titulos")
    elif total > 0 and len(por_curva.get("tasa_fija", [])) == 0:
        print("⚠ ValuacionesAPI sin curva 'tasa_fija'. Revisar seed.")
    else:
        print("✓ Mongo tiene data. Si el HTTP da 200 con docs OK, el problema")
        print("  es ISR cache del frontend (Vercel). Hacer hard-reload en el")
        print("  browser o esperar 60s a que expire el revalidate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
