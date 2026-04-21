"""scripts/test_byma.py — smoke test de BYMA Primarias Placements.

Requiere credenciales en .env:
    BYMA_CLIENT_ID=...
    BYMA_CLIENT_SECRET=...
    BYMA_TOKEN_URL=...
    BYMA_BASE_URL=...

Corre los 4 endpoints uno por uno. Útil para:
- Verificar que las credenciales del .env son válidas (OAuth2 flow).
- Ver el shape real del response (sobre todo historical-placements que
  todavía no tenemos confirmado el formato exacto).
- Descubrir qué filtros acepta historical-placements en la práctica.

Uso:
    /root/TradingAV/venv/bin/python -m scripts.test_byma
    /root/TradingAV/venv/bin/python -m scripts.test_byma --endpoint historical
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from core.byma import (
    BymaError,
    BymaNotConfigured,
    get_access_token,
    get_historical_placements,
    get_issuers,
    get_underwriters,
)


def _pretty(obj: Any, max_chars: int = 2500) -> str:
    s = json.dumps(obj, indent=2, default=str, ensure_ascii=False)
    return s[:max_chars] + (
        f"\n... ({len(s) - max_chars} chars más, cortado)" if len(s) > max_chars else ""
    )


def _header(title: str) -> None:
    print(f"\n─── {title} ".ljust(78, "─"))


def _test_token() -> bool:
    _header("1. OAuth2 token (client_credentials)")
    try:
        tok = get_access_token()
        masked = f"{tok[:16]}...{tok[-8:]}" if len(tok) > 24 else "(corto)"
        print(f"  ✓ token obtenido: {masked}")
        return True
    except BymaNotConfigured as e:
        print(f"  ✗ {e}")
        print("    Revisá .env: BYMA_CLIENT_ID, BYMA_CLIENT_SECRET,")
        print("                 BYMA_TOKEN_URL, BYMA_BASE_URL")
        return False
    except BymaError as e:
        print(f"  ✗ Fallo al autenticar: {e}")
        return False


def _test_underwriters() -> None:
    _header("2. GET /underwriters (page 0, size 10)")
    try:
        resp = get_underwriters(page=0, size=10)
        meta = resp.get("meta") or {}
        total = meta.get("totalItems", "?")
        pages = meta.get("totalPages", "?")
        print(f"  totalItems={total}  totalPages={pages}")
        items = resp.get("result") or []
        print(f"  primeros {min(3, len(items))}:")
        for it in items[:3]:
            print(f"    {it}")
        print("\n  Response completo (trunc):")
        print(_pretty(resp, 1500))
    except BymaError as e:
        print(f"  ✗ {e}")


def _test_issuers() -> None:
    _header("3. GET /issuers (page 0, size 10)")
    try:
        resp = get_issuers(page=0, size=10)
        meta = resp.get("meta") or {}
        total = meta.get("totalItems", "?")
        pages = meta.get("totalPages", "?")
        print(f"  totalItems={total}  totalPages={pages}")
        items = resp.get("result") or []
        print(f"  primeros {min(3, len(items))}:")
        for it in items[:3]:
            print(f"    {it}")
        print("\n  Response completo (trunc):")
        print(_pretty(resp, 1500))
    except BymaError as e:
        print(f"  ✗ {e}")


def _test_historical() -> None:
    _header("4. GET /historical-placements (page 0, size 5)")
    print("  Nota: los parámetros EXACTOS de este endpoint no los tenemos")
    print("  confirmados en la doc. Si el server devuelve 400/422, pegame")
    print("  el error para ajustar la firma.\n")
    try:
        resp = get_historical_placements(page=0, size=5)
        print("  Response completo (trunc):")
        print(_pretty(resp, 3000))
    except BymaError as e:
        print(f"  ✗ {e}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--endpoint",
        choices=["token", "underwriters", "issuers", "historical", "all"],
        default="all",
        help="Qué endpoints testear (default: all)",
    )
    args = parser.parse_args()

    print("=" * 78)
    print("BYMA Primarias Placements — smoke test")
    print("=" * 78)

    if not _test_token():
        print("\nNo se pudo obtener token — abortando resto de tests.")
        return 1

    if args.endpoint in ("all", "underwriters"):
        _test_underwriters()
    if args.endpoint in ("all", "issuers"):
        _test_issuers()
    if args.endpoint in ("all", "historical"):
        _test_historical()

    print("\n" + "=" * 78)
    print("Fin del smoke test. Pegame el output completo.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
