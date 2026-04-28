"""Smoke read-only de los 3 endpoints de cuenta en pyRofex.

Muestra el shape de las 3 respuestas para que decidamos qué campo usar
para el "saldo disponible" de la UI de DOLAR MEP. NO envía nada al broker.

Uso:
    python -m scripts.smoke_account_status
"""
from __future__ import annotations

import json
import sys

import pyRofex
from dotenv import load_dotenv

load_dotenv()

from core.rofex_orders_session import (  # noqa: E402
    cuenta_default,
    inicializar_para_envio,
)


def _print_block(title: str, data: object) -> None:
    print(f"\n{'═' * 80}\n  {title}\n{'═' * 80}")
    print(json.dumps(data, indent=2, default=str, ensure_ascii=False))


def main() -> int:
    inicializar_para_envio()
    account = cuenta_default()
    print(f"\nCuenta: {account}\n")

    for label, fn in [
        ("get_account_report",  lambda: pyRofex.get_account_report(account=account)),
        ("get_account_position", lambda: pyRofex.get_account_position(account=account)),
        ("get_detailed_position", lambda: pyRofex.get_detailed_position(account=account)),
    ]:
        try:
            resp = fn()
            _print_block(label, resp)
        except Exception as e:
            _print_block(f"{label} — ERROR", {"exception": str(e)})

    return 0


if __name__ == "__main__":
    sys.exit(main())
