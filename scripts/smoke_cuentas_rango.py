"""Iterar un rango de cuentas — útil para descubrir cuáles del rango
están autorizadas para el user master y cuáles tienen actividad.

Uso:
    python -m scripts.smoke_cuentas_rango 100 155
    python -m scripts.smoke_cuentas_rango 100 155 --sleep 0.3
    python -m scripts.smoke_cuentas_rango 100 200 --solo-con-saldo

Login con ROFEX_USER_NEW / ROFEX_PASSWORD_NEW / ROFEX_ACCOUNT_NEW. La
cuenta del .env solo se usa para el initialize (puede ser cualquiera
que el user tenga autorizada). Cada N en el rango se prueba con
get_account_report. Si el broker dice "no autorizada", se loggea como
INVÁLIDA y seguimos.

Ojo: cada cuenta pega 2 requests al broker (report + position). 56
cuentas = 112 requests. El sleep default (0.2s) ya da margen, pero si
ves 429 podés subirlo con --sleep.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import pyRofex
from dotenv import load_dotenv

load_dotenv()


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:>16,.2f}"
    return str(v)


def _read(name: str) -> str | None:
    v = (os.getenv(name) or "").strip()
    return v or None


def main() -> int:
    parser = argparse.ArgumentParser(description="Probar un rango de cuentas en el broker")
    parser.add_argument("desde", type=int, help="Cuenta inicial (inclusive)")
    parser.add_argument("hasta", type=int, help="Cuenta final (inclusive)")
    parser.add_argument(
        "--sleep", type=float, default=0.2,
        help="Sleep entre cuentas (s) — default 0.2",
    )
    parser.add_argument(
        "--solo-con-saldo", action="store_true",
        help="Imprimir solo las cuentas que tengan saldo o posiciones",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Cortar después de N cuentas válidas (debug)",
    )
    args = parser.parse_args()

    if args.hasta < args.desde:
        parser.error("hasta debe ser >= desde")

    user = _read("ROFEX_USER_NEW")
    password = _read("ROFEX_PASSWORD_NEW")
    account_login = _read("ROFEX_ACCOUNT_NEW")
    if not (user and password and account_login):
        print("Faltan ROFEX_USER_NEW / ROFEX_PASSWORD_NEW / ROFEX_ACCOUNT_NEW en .env")
        return 2

    api_url = (os.getenv("ROFEX_API_URL") or "").strip()
    ws_url = (os.getenv("ROFEX_WS_URL") or "").strip()

    rango = list(range(args.desde, args.hasta + 1))
    print(f"\n  user:   {user}")
    print(f"  rango:  {args.desde} … {args.hasta}  ({len(rango)} cuentas)")
    print(f"  sleep:  {args.sleep}s")
    print(f"  filtro: {'solo con saldo' if args.solo_con_saldo else 'todas'}\n")

    if api_url:
        pyRofex._set_environment_parameter("url", api_url, pyRofex.Environment.LIVE)
    if ws_url:
        pyRofex._set_environment_parameter("ws", ws_url, pyRofex.Environment.LIVE)

    try:
        pyRofex.initialize(
            user=user, password=password, account=account_login,
            environment=pyRofex.Environment.LIVE,
        )
        print("login: ✓\n")
    except Exception as e:
        print(f"login: ✗ {e}")
        return 1

    print("═" * 92)
    header = f"  {'cuenta':<10} {'ARS available':>18} {'USD D available':>18} {'pos #':>8} {'estado':<28}"
    print(header)
    print("  " + "-" * 88)

    n_validas = 0
    n_con_saldo = 0
    n_invalidas = 0
    n_error = 0
    candidatas: list[dict] = []

    for acc_int in rango:
        acc = str(acc_int)
        ars = usd_d = None
        n_pos = 0
        estado = "?"
        valida = False
        con_saldo = False

        try:
            rpt = pyRofex.get_account_report(account=acc)
            if rpt and rpt.get("status") == "OK":
                valida = True
                n_validas += 1
                ad = rpt.get("accountData") or {}
                settle_ci = (ad.get("detailedAccountReports") or {}).get("0") or {}
                cb = (settle_ci.get("currencyBalance") or {}).get("detailedCurrencyBalance") or {}
                ars = (cb.get("ARS") or {}).get("available")
                usd_d = (cb.get("USD D") or {}).get("available")
                estado = "OK"
            else:
                desc = (rpt or {}).get("description", "")
                if "no autorizada" in desc.lower() or "not authorized" in desc.lower() or "invalid" in desc.lower():
                    estado = "no autorizada"
                    n_invalidas += 1
                else:
                    estado = f"err: {desc[:24]}"
                    n_error += 1
        except Exception as e:
            estado = f"exc: {str(e)[:24]}"
            n_error += 1

        if valida:
            try:
                pos = pyRofex.get_account_position(account=acc)
                if pos and pos.get("status") == "OK":
                    n_pos = len(pos.get("positions") or [])
            except Exception:
                pass

        con_saldo = bool((ars or 0) != 0 or (usd_d or 0) != 0 or n_pos > 0)
        if con_saldo:
            n_con_saldo += 1
            candidatas.append({"acc": acc, "ars": ars, "usd_d": usd_d, "n_pos": n_pos})

        if (not args.solo_con_saldo) or con_saldo:
            line = f"  {acc:<10} {_fmt(ars):>18} {_fmt(usd_d):>18} {n_pos:>8} {estado:<28}"
            print(line)

        if args.limit and n_validas >= args.limit:
            print(f"\n  --limit {args.limit} alcanzado, corto acá.")
            break

        time.sleep(args.sleep)

    print("═" * 92)
    print(f"\n  Resumen: {n_validas} válidas · {n_con_saldo} con actividad · "
          f"{n_invalidas} no autorizadas · {n_error} errores")

    if candidatas:
        print("\n  Cuentas con saldo / posiciones (ordenadas por ARS):")
        for c in sorted(candidatas, key=lambda x: -(x["ars"] or 0)):
            print(f"    · {c['acc']:<6}  ARS={_fmt(c['ars'])}  USD D={_fmt(c['usd_d'])}  pos={c['n_pos']}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
