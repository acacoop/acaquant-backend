"""Diagnóstico de cada endpoint /api/smart-money/* en localhost:8000.

Para cada endpoint mide: HTTP status, latencia, tamaño del body y los primeros
chars del body (útil para ver el detail de error 5xx o el shape OK).

Auth: lee API_KEY del .env local del Droplet automáticamente. No tenés que
pasar nada — la key nunca sale del servidor.

Objetivo: identificar QUÉ endpoint específico tira 5xx y cuál anda. Si alguno
tarda >10s, ese es el que Vercel mata con 504 (Hobby limit) que el cliente
ve como 502.

Uso:
    python -m scripts.diag_smart_money_endpoints
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

import requests


def _read_env_var(name: str) -> str | None:
    """Lee `name` de os.environ, .env files, o systemd Environment del api.service."""
    v = os.environ.get(name)
    if v:
        return v
    for path in ("/root/TradingAV/.env", "/root/.env"):
        try:
            with open(path) as f:
                for line in f:
                    m = re.match(rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$", line)
                    if m:
                        v = m.group(1).strip("'\"")
                        if v:
                            return v
        except FileNotFoundError:
            continue
    try:
        r = subprocess.run(
            ["systemctl", "show", "api.service", "-p", "Environment"],
            capture_output=True, text=True, timeout=5,
        )
        m = re.search(rf"{re.escape(name)}=([^\s]+)", r.stdout)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _read_api_key() -> str | None:
    return _read_env_var("API_KEY")


def _read_admin_email() -> str | None:
    """Email admin para mandar como x-acaquant-user-email — sino el RBAC asume DEFAULT_ROLE (sales).

    Prioridad: DIAG_ADMIN_EMAIL > primer email de MANAGER_EMAILS.
    """
    explicit = _read_env_var("DIAG_ADMIN_EMAIL")
    if explicit:
        return explicit.strip().lower()
    managers = _read_env_var("MANAGER_EMAILS")
    if managers:
        first = managers.split(",")[0].strip().lower()
        if first:
            return first
    return None


def _hit(label: str, path: str, api_key: str, admin_email: str | None = None) -> None:
    url = f"http://127.0.0.1:8000{path}"
    headers = {"Authorization": f"Bearer {api_key}"}
    if admin_email:
        # Sin esto el RBAC asume DEFAULT_ROLE (sales) y módulos restringidos
        # como renta-variable devuelven 403. El header propaga el email para
        # que `get_user_role` resuelva el role real desde Manager.Users.
        headers["x-acaquant-user-email"] = admin_email
    t0 = time.time()
    try:
        r = requests.get(url, headers=headers, timeout=30)
        elapsed = time.time() - t0
        size = len(r.content)
        ok = "✓" if r.status_code == 200 else "✗"
        # Resumen: status, tiempo, size
        print(
            f"  {ok}  {r.status_code:<4}  {elapsed*1000:>6.0f}ms  "
            f"{size/1024:>6.1f}KB  {label}"
        )
        if r.status_code != 200:
            print(f"     body (recortado): {r.text[:400]}")
            return
        # Para 200, mostrar resumen estructural del JSON
        try:
            j = r.json()
            _resumen_estructura(label, j)
        except json.JSONDecodeError:
            print(f"     respuesta no-JSON: {r.text[:200]}")
    except requests.exceptions.Timeout:
        elapsed = time.time() - t0
        print(f"  ⏱   TIMEOUT  {elapsed*1000:>6.0f}ms (>30s)  {label}")
    except requests.exceptions.ConnectionError:
        print(f"  ✗  CONNECTION  ?  ?  {label}  — ¿api.service caído?")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ✗  EXCEPTION  {elapsed*1000:>6.0f}ms  {label}  → {type(e).__name__}: {e}")


def _resumen_estructura(label: str, j) -> None:
    """Imprime shape básico para detectar respuestas con data anómala."""
    if isinstance(j, list):
        print(f"     list[{len(j)}]" + (f"  sample[0]: keys={list(j[0].keys())[:6]}" if j and isinstance(j[0], dict) else ""))
    elif isinstance(j, dict):
        keys = list(j.keys())
        print(f"     dict keys: {keys[:8]}")
        # Pistas específicas
        if "n_managers" in j.get("institutional_13f", {}):
            inst = j["institutional_13f"]
            print(f"     institutional_13f: n_managers={inst.get('n_managers')}  total_value={inst.get('total_value_usd')}")
        if "top_buys" in j:
            print(f"     top_buys count={len(j.get('top_buys') or [])}, top_sells count={len(j.get('top_sells') or [])}")
        if "n_holdings_cedear" in j:
            print(f"     n_holdings_cedear={j.get('n_holdings_cedear')}, current_quarter={j.get('current_quarter')}")
        if "transactions" in j:
            print(f"     transactions count={len(j.get('transactions') or [])}")


def run() -> None:
    api_key = _read_api_key()
    if not api_key:
        print("✗ No encontré API_KEY (.env, systemd Environment).")
        return
    print(f"API_KEY: ***{api_key[-4:]} (encontrada)")

    admin_email = _read_admin_email()
    if admin_email:
        print(f"ADMIN_EMAIL: {admin_email} (header x-acaquant-user-email)")
    else:
        print("⚠ ADMIN_EMAIL: no encontrado — los endpoints renta-variable van a tirar 403.")
        print("  Set DIAG_ADMIN_EMAIL=<tu_email_admin> o agregalo a MANAGER_EMAILS.")
    print()

    print(f"  {'OK':<3}  {'HTTP':<4}  {'TIME':>8}  {'SIZE':>8}  ENDPOINT")
    print("  " + "─" * 100)

    # Endpoints sin parámetros
    _hit("/api/smart-money/catalog",          "/api/smart-money/catalog", api_key, admin_email)
    _hit("/api/smart-money/managers",         "/api/smart-money/managers", api_key, admin_email)
    _hit("/api/smart-money/cohort-overview",  "/api/smart-money/cohort-overview", api_key, admin_email)
    _hit("/api/smart-money/recent-activity",  "/api/smart-money/recent-activity?days=7", api_key, admin_email)

    # Endpoints con parámetros (probamos con Berkshire + AAPL)
    _hit(
        "/api/smart-money/manager/1067983  (Berkshire)",
        "/api/smart-money/manager/1067983",
        api_key, admin_email,
    )
    _hit(
        "/api/smart-money/ticker/AAPL",
        "/api/smart-money/ticker/AAPL",
        api_key, admin_email,
    )
    _hit(
        "/api/smart-money/ticker/NVDA",
        "/api/smart-money/ticker/NVDA",
        api_key, admin_email,
    )

    print("\nNotas:")
    print(" - Si algún endpoint tarda >10s → Vercel Hobby lo mata con 504, cliente ve 502.")
    print(" - Si HTTP 5xx con detail → bug en el service (mirá el detail).")
    print(" - Si HTTP 200 pero data rara → bug en la query/heurística, no en el endpoint.")


if __name__ == "__main__":
    run()
