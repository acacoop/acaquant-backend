"""scripts/diag_entorno.py — ¿el entorno real corre lo que pinea requirements.txt?

READ-ONLY. Nace del hallazgo 2026-08-22 (AV_AGENT.md §0.ci): `api/superficie.py`
fue calibrada contra un `app.routes` que devuelve envoltorios `_IncludedRouter`
(así se midió el 37/541), y la FastAPI que pinea `requirements.txt` (0.136.x)
NO tiene esa clase — aplana las rutas. O sea que el entorno donde corre el
sistema y el que instala CI no son el mismo, y todo lo que dependa del
comportamiento de FastAPI (el mapa AUTOGEN, la superficie de seguridad) se
genera distinto según dónde corra. REGLA #9(B): dos entornos sin árbitro.

Correr en el Droplet (y en la máquina local si se genera el mapa ahí):

    python -m scripts.diag_entorno

Compara lo INSTALADO contra lo PINEADO y dice en qué mundo cae `app.routes`.
"""
from __future__ import annotations

import importlib.metadata as md
import re
import sys
from pathlib import Path

PAQUETES = ["fastapi", "starlette", "pydantic", "psycopg", "psycopg-pool",
            "uvicorn", "httpx"]


def _pineadas() -> dict[str, str]:
    req = Path(__file__).resolve().parents[1] / "requirements.txt"
    out: dict[str, str] = {}
    for linea in req.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\[\]-]+)==([\w.]+)", linea.strip())
        if m:
            out[m.group(1).split("[")[0].lower()] = m.group(2)
    return out


def main() -> None:
    print(f"python {sys.version.split()[0]}")
    pins = _pineadas()
    hay_drift = False
    for p in PAQUETES:
        try:
            inst = md.version(p)
        except md.PackageNotFoundError:
            inst = "(no instalado)"
        pin = pins.get(p, "(sin pin)")
        marca = "" if inst == pin else "   ← DIFIERE del pin"
        if inst != pin:
            hay_drift = True
        print(f"  {p:<12} instalado {inst:<12} pin {pin}{marca}")

    # ¿En qué mundo cae app.routes? Se prueba con una app mínima, sin tocar
    # api.main (esto tiene que poder correr aunque la API esté rota).
    from fastapi import APIRouter, FastAPI
    app, r = FastAPI(), APIRouter()
    r.add_api_route("/x", lambda: None)
    app.include_router(r)
    tipos = {type(x).__name__ for x in app.routes}
    lazy = "_IncludedRouter" in tipos
    print(f"\napp.routes: {'ENVOLTORIOS (lazy)' if lazy else 'PLANO'} — "
          f"superficie/gen_mapa_app se comportan según esto")
    if hay_drift:
        print("\n⚠️ Hay drift instalado-vs-pin: el mapa AUTOGEN y el chequeo de")
        print("   superficie se generan distinto acá que en CI. Decidir cuál de")
        print("   los dos manda y alinear el otro (AV_AGENT.md §0.ci).")
    else:
        print("\n✅ Instalado == pineado en los paquetes clave.")


if __name__ == "__main__":
    main()
