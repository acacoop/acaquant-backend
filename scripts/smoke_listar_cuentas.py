"""Listar todas las cuentas asociadas al user nuevo (ROFEX_USER_NEW).

Después de smoke_creds_paralelas, este script reusa esas mismas
credenciales y prueba los endpoints REST del broker que típicamente
exponen la lista de cuentas asociadas al user master. Los brokers tipo
Primary / xoms (ACA Valores incluido) no tienen un método único en
pyRofex para esto, así que probamos varios candidatos y mostramos cuál
responde con datos útiles.

Nada de escritura — solo GETs autenticados con el X-Auth-Token que
pyRofex deja en su cliente REST después de initialize().

Uso:
    python -m scripts.smoke_listar_cuentas
"""
from __future__ import annotations

import json
import os
import sys

import pyRofex
import pyRofex.components.globals as pyrofex_globals
import requests
from dotenv import load_dotenv

load_dotenv()

# Endpoints REST que vale la pena probar. La mayoría siguen la convención
# Primary; algunos están en xoms (ACA Valores) por ser fork. Si ninguno
# devuelve OK, el broker no expone el listado y hay que pedirlo a soporte.
def _build_endpoints(user: str, account: str) -> list[str]:
    """Lista de endpoints a probar. Algunos esperan el user / account en
    el path, otros son query params. La pista buena es que
    `rest/risk/accountReport/all` tiró NullPointerException (HTTP 200) —
    eso significa que existe pero le falta un argumento.
    """
    return [
        # /accountReport/all + variantes con user / account
        "rest/risk/accountReport/all",
        f"rest/risk/accountReport/all/{user}",
        f"rest/risk/accountReport/all/{account}",
        f"rest/risk/accountReport/all?user={user}",
        f"rest/risk/accountReport/all?account={account}",
        # /accountReport singular con identidades del user
        f"rest/risk/accountReport/{user}",
        f"rest/risk/accountReport/{account}",
        # /detailedPosition/all (algunos brokers)
        "rest/risk/detailedPosition/all",
        f"rest/risk/detailedPosition/all/{user}",
        # auth/getAccounts variantes
        "rest/auth/getAccounts",
        f"rest/auth/getAccounts?user={user}",
        f"rest/auth/getAccounts/{user}",
        # otros caminos
        "rest/account/all",
        "rest/account/list",
        "rest/risk/accounts",
        f"rest/users/{user}/accounts",
    ]


def _read(name: str) -> str | None:
    v = (os.getenv(name) or "").strip()
    return v or None


def main() -> int:
    user = _read("ROFEX_USER_NEW")
    password = _read("ROFEX_PASSWORD_NEW")
    account = _read("ROFEX_ACCOUNT_NEW")
    if not (user and password and account):
        print("Faltan ROFEX_USER_NEW / ROFEX_PASSWORD_NEW / ROFEX_ACCOUNT_NEW en .env")
        return 2

    api_url = (os.getenv("ROFEX_API_URL") or "").strip()
    ws_url = (os.getenv("ROFEX_WS_URL") or "").strip()

    print(f"\n  user:    {user}")
    print(f"  account: {account}")
    print(f"  url:     {api_url or 'default LIVE'}\n")

    if api_url:
        pyRofex._set_environment_parameter("url", api_url, pyRofex.Environment.LIVE)
    if ws_url:
        pyRofex._set_environment_parameter("ws", ws_url, pyRofex.Environment.LIVE)

    try:
        pyRofex.initialize(
            user=user, password=password, account=account,
            environment=pyRofex.Environment.LIVE,
        )
        print("login: ✓\n")
    except Exception as e:
        print(f"login: ✗ {e}")
        return 1

    # pyRofex deja el token y el cliente REST acá.
    cfg = pyrofex_globals.environment_config[pyRofex.Environment.LIVE]
    token = cfg.get("token")
    url_base = cfg["url"].rstrip("/")
    ssl_verify = cfg.get("ssl", True)
    proxies = cfg.get("proxies")

    if not token:
        print("⚠ pyRofex no expuso el token después del login — no puedo probar endpoints.")
        return 1

    headers = {"X-Auth-Token": token}

    print("─" * 72)
    print("  Probando endpoints REST conocidos…")
    print("─" * 72)

    encontrados: list[tuple[str, dict | list]] = []
    for ep in _build_endpoints(user, account):
        full = f"{url_base}/{ep}"
        try:
            r = requests.get(
                full, headers=headers, verify=ssl_verify, proxies=proxies, timeout=10,
            )
            if r.ok:
                try:
                    data = r.json()
                except ValueError:
                    print(f"  {ep}: HTTP {r.status_code} (no-JSON, len={len(r.text)})")
                    continue
                # Algunos brokers devuelven {status:"ERROR", description:...}
                # con HTTP 200 — los marcamos como no-útiles.
                if isinstance(data, dict) and data.get("status") == "ERROR":
                    print(f"  {ep}: HTTP 200 pero status=ERROR — {data.get('description')}")
                else:
                    encontrados.append((ep, data))
                    print(f"  ✓ {ep}: HTTP {r.status_code} OK")
            else:
                print(f"  ✗ {ep}: HTTP {r.status_code}")
        except Exception as e:
            print(f"  ✗ {ep}: {e}")

    if not encontrados:
        print("\n⚠ Ningún endpoint devolvió cuentas.")
        print("  Probá pedirle a soporte de ACA / xoms el endpoint exacto,")
        print("  o pegale al panel web del broker para extraer la lista a mano.")
        return 1

    print("\n" + "═" * 72)
    print("  RESULTADO — payloads útiles:")
    print("═" * 72)
    for ep, data in encontrados:
        print(f"\n→ {ep}")
        # Pintamos un dump compacto. Si es lista de objetos, intentamos
        # extraer "name" / "id" / "account" para que sea legible.
        if isinstance(data, list):
            print(f"  ({len(data)} entradas)")
            for entry in data[:30]:
                if isinstance(entry, dict):
                    keys = ("id", "name", "account", "accountName", "alias")
                    short = {k: entry[k] for k in keys if k in entry}
                    print(f"    {short or entry}")
                else:
                    print(f"    {entry}")
            if len(data) > 30:
                print(f"    … y {len(data) - 30} más")
        elif isinstance(data, dict):
            # Si tiene una key "accounts" típica
            if "accounts" in data and isinstance(data["accounts"], list):
                accs = data["accounts"]
                print(f"  accounts: ({len(accs)} entradas)")
                for a in accs[:30]:
                    print(f"    {a}")
                if len(accs) > 30:
                    print(f"    … y {len(accs) - 30} más")
            else:
                # Dump compacto
                print("  " + json.dumps(data, indent=2, default=str)[:1500])
        else:
            print(f"  {data}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
