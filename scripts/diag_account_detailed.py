"""Diagnóstico — qué shape devuelven `get_account_report` y
`get_detailed_position` para una cuenta. Sin ver el shape real no se puede
parsear tenencias en el panel PORTFOLIO del Dashboard.

Uso:
    cd /root/TradingAV && /root/TradingAV/venv/bin/python -m scripts.diag_account_detailed --account 805
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

import pyRofex
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("diag_account_detailed")


def _login() -> str:
    user = os.getenv("ROFEX_USER") or ""
    password = os.getenv("ROFEX_PASSWORD") or ""
    account = os.getenv("ROFEX_ACCOUNT") or ""
    if not (user and password and account):
        raise RuntimeError("Faltan ROFEX_USER / ROFEX_PASSWORD / ROFEX_ACCOUNT en .env")

    api_url = (os.getenv("ROFEX_API_URL") or "").strip()
    ws_url = (os.getenv("ROFEX_WS_URL") or "").strip()
    if api_url:
        pyRofex._set_environment_parameter("url", api_url, pyRofex.Environment.LIVE)
    if ws_url:
        pyRofex._set_environment_parameter("ws", ws_url, pyRofex.Environment.LIVE)

    pyRofex.initialize(
        user=user, password=password, account=account,
        environment=pyRofex.Environment.LIVE,
    )
    logger.info("pyRofex inicializado (user=%s account_login=%s)", user, account)
    return account


def _dump(label: str, obj) -> None:
    print("\n" + "=" * 70)
    print(f">>> {label}")
    print("=" * 70)
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True, help="Cuenta a inspeccionar (ej. 805)")
    args = parser.parse_args()

    _login()
    acc = args.account

    # 1) get_account_report — el que ya sabemos que trae saldos OK.
    try:
        rpt = pyRofex.get_account_report(account=acc)
        _dump(f"get_account_report(account={acc})", rpt)
    except Exception as e:
        logger.error("get_account_report exc: %s", e)

    # 2) get_detailed_position — el que usamos en el panel PORTFOLIO.
    try:
        dp = pyRofex.get_detailed_position(account=acc)
        _dump(f"get_detailed_position(account={acc})", dp)
    except Exception as e:
        logger.error("get_detailed_position exc: %s", e)

    # 3) get_account_position — versión "agregada" (menos detallada).
    try:
        ap = pyRofex.get_account_position(account=acc)
        _dump(f"get_account_position(account={acc})", ap)
    except Exception as e:
        logger.error("get_account_position exc: %s", e)

    print("\n" + "=" * 70)
    print("FIN. Pegame los 3 dumps en el chat (o solo el de get_detailed_position")
    print("si es el más completo) y arreglo el parseo del PortfolioPanel.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
