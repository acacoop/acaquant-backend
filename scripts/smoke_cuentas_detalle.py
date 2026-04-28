"""Detalle por cuenta — itera las cuentas asociadas al user master.

Cuando ROFEX_ACCOUNT_NEW es CSV (`1839,255,101,100`), pyRofex acepta el
login con todas pero `get_account_report` necesita una cuenta sola. Este
script splitea, hace 1 report por cuenta, y muestra una tabla resumen
para que sepas cuál es la "operativa" (la que tiene saldo / posiciones).

Uso:
    # Asegurate que ROFEX_ACCOUNT_NEW="1839,255,101,100" en .env
    python -m scripts.smoke_cuentas_detalle

Solo lee, no escribe.
"""
from __future__ import annotations

import os
import sys

import pyRofex
from dotenv import load_dotenv

load_dotenv()


def _read(name: str) -> str | None:
    v = (os.getenv(name) or "").strip()
    return v or None


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:>16,.2f}"
    return str(v)


def main() -> int:
    user = _read("ROFEX_USER_NEW")
    password = _read("ROFEX_PASSWORD_NEW")
    accounts_csv = _read("ROFEX_ACCOUNT_NEW")
    if not (user and password and accounts_csv):
        print("Faltan ROFEX_USER_NEW / ROFEX_PASSWORD_NEW / ROFEX_ACCOUNT_NEW en .env")
        return 2

    api_url = (os.getenv("ROFEX_API_URL") or "").strip()
    ws_url = (os.getenv("ROFEX_WS_URL") or "").strip()

    accounts = [a.strip() for a in accounts_csv.split(",") if a.strip()]
    print(f"\n  user:     {user}")
    print(f"  cuentas:  {accounts}\n")

    if api_url:
        pyRofex._set_environment_parameter("url", api_url, pyRofex.Environment.LIVE)
    if ws_url:
        pyRofex._set_environment_parameter("ws", ws_url, pyRofex.Environment.LIVE)

    try:
        pyRofex.initialize(
            user=user, password=password, account=accounts_csv,
            environment=pyRofex.Environment.LIVE,
        )
        print("login: ✓\n")
    except Exception as e:
        print(f"login: ✗ {e}")
        return 1

    print("═" * 90)
    print("  RESUMEN POR CUENTA — currencyBalance.detailedCurrencyBalance (CI = settle 0)")
    print("═" * 90)
    header = f"  {'cuenta':<10} {'ARS available':>18} {'USD D available':>18} {'pos #':>8} {'totalMV':>20}"
    print(header)
    print("  " + "-" * 86)

    encontradas: list[dict] = []

    for acc in accounts:
        ars = usd_d = total_mv = None
        n_pos = 0
        err = None
        try:
            rpt = pyRofex.get_account_report(account=acc)
            if rpt and rpt.get("status") == "OK":
                ad = rpt.get("accountData") or {}
                settle_ci = (ad.get("detailedAccountReports") or {}).get("0") or {}
                cb = (settle_ci.get("currencyBalance") or {}).get("detailedCurrencyBalance") or {}
                ars = (cb.get("ARS") or {}).get("available")
                usd_d = (cb.get("USD D") or {}).get("available")
            else:
                err = (rpt or {}).get("description") or "status no-OK"
        except Exception as e:
            err = str(e)

        try:
            pos = pyRofex.get_account_position(account=acc)
            if pos and pos.get("status") == "OK":
                n_pos = len(pos.get("positions") or [])
        except Exception:
            pass

        try:
            det = pyRofex.get_detailed_position(account=acc)
            if det and det.get("status") == "OK":
                total_mv = (det.get("detailedPosition") or {}).get("totalMarketValue")
        except Exception:
            pass

        line = f"  {acc:<10} {_fmt(ars):>18} {_fmt(usd_d):>18} {n_pos:>8} {_fmt(total_mv):>20}"
        if err:
            line += f"   ⚠ {err}"
        print(line)

        encontradas.append({
            "account":  acc,
            "ars":      ars,
            "usd_d":    usd_d,
            "n_pos":    n_pos,
            "total_mv": total_mv,
            "err":      err,
        })

    print("═" * 90)

    # Sugerimos la "operativa": la que tenga ARS > 0 o USD D != 0 o
    # más posiciones. Si hay una sola con datos, esa es.
    candidatas = [
        c for c in encontradas
        if (c["ars"] or 0) > 0 or (c["usd_d"] or 0) != 0 or c["n_pos"] > 0 or (c["total_mv"] or 0) != 0
    ]
    if not candidatas:
        print("\n⚠ Ninguna cuenta reporta saldo/posiciones. Capaz hay que mirar otros settle types,")
        print("  o las cuentas son holding-only y la operativa va por otra vía.")
    elif len(candidatas) == 1:
        c = candidatas[0]
        print(f"\n→ Cuenta operativa probable: {c['account']}")
        print(f"  ARS={_fmt(c['ars'])}  USD D={_fmt(c['usd_d'])}  posiciones={c['n_pos']}  totalMV={_fmt(c['total_mv'])}")
    else:
        print(f"\n→ {len(candidatas)} cuentas con actividad — definir cuál es la default operativa:")
        for c in candidatas:
            print(f"    · {c['account']}  ARS={_fmt(c['ars'])} USD D={_fmt(c['usd_d'])} pos={c['n_pos']} mv={_fmt(c['total_mv'])}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
