"""Diagnóstico del proceso api.service: estado + log reciente + traceback.

Cuando los endpoints tiran ConnectionError es porque el proceso uvicorn está
caído (no porque las rutas estén mal). Esto muestra:

  1. systemctl status api.service                → Active/failed + últimos logs
  2. journalctl -u api.service -n 50 --no-pager  → 50 líneas de log reciente
  3. ss -tlnp 'sport = :8000'                    → ¿algo escucha el puerto?

Uso:
    python -m scripts.diag_api_service_status
"""
from __future__ import annotations

import subprocess


def _run(cmd: list[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        return out
    except subprocess.TimeoutExpired:
        return "<TIMEOUT>"
    except FileNotFoundError:
        return f"<COMMAND NOT FOUND: {cmd[0]}>"
    except Exception as e:
        return f"<ERROR: {e}>"


def run() -> None:
    print("=" * 80)
    print("A. systemctl status api.service")
    print("=" * 80)
    print(_run(["systemctl", "status", "api.service", "--no-pager", "-l"]))

    print("=" * 80)
    print("B. journalctl -u api.service — últimas 80 líneas")
    print("=" * 80)
    print(_run(["journalctl", "-u", "api.service", "-n", "80", "--no-pager"]))

    print("=" * 80)
    print("C. ¿Algo escucha el puerto 8000?")
    print("=" * 80)
    print(_run(["ss", "-tlnp", "sport = :8000"]))

    print("\nQué buscar:")
    print("  - En (A) → 'Active: failed' o 'inactive' → el proceso está caído")
    print("  - En (B) → buscá 'Traceback' o 'ImportError' o 'Error' al final")
    print("  - En (C) → si no aparece ':8000' nadie está escuchando")


if __name__ == "__main__":
    run()
