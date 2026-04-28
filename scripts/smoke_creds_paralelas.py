"""Smoke read-only de credenciales nuevas (ROFEX_*_NEW del .env).

Permite verificar que las credenciales nuevas funcionan ANTES de hacer el
cutover. Corre como proceso aparte de uvicorn / motores → no contamina
la sesión pyRofex que está atendiendo en producción.

Uso:
    # Agregar al .env del Droplet (las viejas siguen tal cual):
    #   ROFEX_USER_NEW=...
    #   ROFEX_PASSWORD_NEW=...
    #   ROFEX_ACCOUNT_NEW=...
    # Después correr:
    python -m scripts.smoke_creds_paralelas

NO envía órdenes ni cancela nada. Lee:
    - login (initialize)
    - get_account_report
    - get_account_position (qué tickers tiene la cuenta)
    - get_market_data (un ticker líquido, AL30)

Si todo da OK, sigue las instrucciones que imprime al final para hacer
el cutover sin downtime perceptible.
"""
from __future__ import annotations

import os
import sys

import pyRofex
from dotenv import load_dotenv

load_dotenv()


def _read_var(name: str) -> str | None:
    v = (os.getenv(name) or "").strip()
    if not v:
        print(f"  ✗ Falta {name} en .env")
        return None
    return v


def main() -> int:
    print("─" * 72)
    print("  SMOKE — credenciales nuevas (ROFEX_*_NEW)")
    print("─" * 72)

    user = _read_var("ROFEX_USER_NEW")
    password = _read_var("ROFEX_PASSWORD_NEW")
    account = _read_var("ROFEX_ACCOUNT_NEW")
    if not (user and password and account):
        print(
            "\n→ Agregá ROFEX_USER_NEW / ROFEX_PASSWORD_NEW / ROFEX_ACCOUNT_NEW "
            "al .env y volvé a correr."
        )
        return 2

    print(f"\n  user:    {user}")
    print(f"  account: {account}")
    print("  env:     LIVE\n")

    # URLs custom (paridad con core/rofex_orders_session) — si están en
    # el .env las usamos para el environment LIVE de este proceso.
    api_url = (os.getenv("ROFEX_API_URL") or "").strip()
    ws_url = (os.getenv("ROFEX_WS_URL") or "").strip()
    if api_url:
        pyRofex._set_environment_parameter("url", api_url, pyRofex.Environment.LIVE)
        print(f"  API URL: {api_url}")
    if ws_url:
        pyRofex._set_environment_parameter("ws", ws_url, pyRofex.Environment.LIVE)
        print(f"  WS URL:  {ws_url}")

    # 1) Login
    print("\n[1/4] initialize …")
    try:
        pyRofex.initialize(
            user=user, password=password, account=account,
            environment=pyRofex.Environment.LIVE,
        )
        print("      ✓ login OK")
    except Exception as e:
        print(f"      ✗ login FALLÓ: {e}")
        return 1

    # 2) account_report
    print("[2/4] get_account_report …")
    try:
        rpt = pyRofex.get_account_report(account=account)
        if not rpt or rpt.get("status") != "OK":
            print(f"      ✗ status no-OK: {rpt}")
            return 1
        ad = rpt.get("accountData") or {}
        settle_ci = (ad.get("detailedAccountReports") or {}).get("0") or {}
        cb = (settle_ci.get("currencyBalance") or {}).get("detailedCurrencyBalance") or {}
        ars = (cb.get("ARS") or {}).get("available")
        usd_d = (cb.get("USD D") or {}).get("available")
        print(f"      ✓ ARS disponible CI:   {ars}")
        print(f"      ✓ USD D disponible CI: {usd_d}")
    except Exception as e:
        print(f"      ✗ excepción: {e}")
        return 1

    # 3) account_position
    print("[3/4] get_account_position …")
    try:
        pos = pyRofex.get_account_position(account=account)
        if not pos or pos.get("status") != "OK":
            print(f"      ✗ status no-OK: {pos}")
            return 1
        n = len(pos.get("positions") or [])
        print(f"      ✓ {n} posiciones reportadas")
    except Exception as e:
        print(f"      ✗ excepción: {e}")
        return 1

    # 4) market_data de un ticker líquido — read-only
    print("[4/4] get_market_data AL30 …")
    try:
        md = pyRofex.get_market_data(
            ticker="MERV - XMEV - AL30 - 24hs",
            entries=[pyRofex.MarketDataEntry.LAST],
        )
        if md and md.get("status") == "OK":
            last = (md.get("marketData") or {}).get("LA") or {}
            px = last.get("price")
            sz = last.get("size")
            print(f"      ✓ AL30 last: ${px} × {sz}")
        else:
            # Si el broker no devuelve datos (fuera de rueda, etc.) no
            # marcamos como error — no es bloqueante para el cutover.
            print(f"      ⚠ sin datos (puede ser fuera de rueda): {md}")
    except Exception as e:
        print(f"      ⚠ excepción: {e}")

    print("\n" + "─" * 72)
    print("  ✅ SMOKE OK — las credenciales nuevas funcionan.")
    print("─" * 72)
    print("\n  Cutover (sin downtime perceptible):")
    print("    1) Editar /root/TradingAV/.env:")
    print("         ROFEX_USER       ← valor de ROFEX_USER_NEW")
    print("         ROFEX_PASSWORD   ← valor de ROFEX_PASSWORD_NEW")
    print("         ROFEX_ACCOUNT    ← valor de ROFEX_ACCOUNT_NEW")
    print("       Después podés borrar las _NEW.")
    print("    2) Reiniciar los servicios que tocan pyRofex (~3-5s c/u):")
    print("         systemctl restart api.service")
    print("         systemctl restart motor_ordenes.service")
    print("         systemctl restart motor_rofex.service")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
