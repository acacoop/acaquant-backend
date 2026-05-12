"""Diag de servicios smart_money llamados DIRECTO (sin HTTP).

Objetivo: ver el traceback completo de las llamadas que vía API devuelven
500. El router enmascara la excepción como "Internal Server Error" sin
detalle; llamar a `api.services.smart_money` directo nos da la línea exacta.

Uso:
    python -m scripts.diag_smart_money_services
"""
from __future__ import annotations

import json
import traceback
from typing import Any

from api.services import smart_money as svc

CASES: list[tuple[str, callable]] = [
    ("get_managers_list()",            lambda: svc.get_managers_list()),
    ("get_cohort_overview()",          lambda: svc.get_cohort_overview()),
    ("get_recent_activity(days=7)",    lambda: svc.get_recent_activity(days=7)),
    ("get_manager_portfolio('1067983') [Berkshire]", lambda: svc.get_manager_portfolio("1067983")),
    ("get_ticker_flow('AAPL')",        lambda: svc.get_ticker_flow("AAPL")),
    ("get_ticker_flow('NVDA')",        lambda: svc.get_ticker_flow("NVDA")),
]


def _summary(res: Any) -> str:
    if isinstance(res, dict):
        keys = list(res.keys())
        if "error" in res:
            return f"dict[error]: {res['error']}"
        return f"dict keys={keys[:8]}{'...' if len(keys) > 8 else ''}"
    if isinstance(res, list):
        return f"list[{len(res)}]"
    return f"{type(res).__name__}"


def run() -> None:
    print("=" * 100)
    print("DIAG smart_money services (llamadas directas, sin HTTP)")
    print("=" * 100)
    for name, fn in CASES:
        print(f"\n── {name}")
        try:
            res = fn()
            print(f"   OK → {_summary(res)}")
            # Para los que rompen vía HTTP, dump completo del shape para ver
            # qué fields trae y si algo no es JSON-serializable.
            if name.startswith(("get_manager_portfolio", "get_ticker_flow")):
                try:
                    json.dumps(res, default=str)
                    print("   JSON-serializable: OK (con default=str)")
                    # Probemos sin default — esto detecta Decimal128 / datetime no soportados
                    try:
                        json.dumps(res)
                        print("   JSON-serializable: OK (default JSONEncoder)")
                    except TypeError as e:
                        print(f"   ⚠ default JSONEncoder FALLA: {e}")
                        print("   → este es probablemente el bug 500 (FastAPI no puede serializar)")
                except Exception as e:
                    print(f"   ⚠ json.dumps con default=str también falla: {e}")
        except Exception as e:
            print(f"   ✗ EXCEPCIÓN: {type(e).__name__}: {e}")
            print("   ─ traceback ─")
            for line in traceback.format_exc().splitlines():
                print(f"   {line}")

    print("\n" + "=" * 100)


if __name__ == "__main__":
    run()
