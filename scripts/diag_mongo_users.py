"""diag_mongo_users.py — qué usuario de Mongo usa cada URI del proyecto.

Lee el `.env` de la raíz del repo (mismo que carga `core/mongo.py`) y parsea
los 3 vars que contienen URIs a Atlas:

  MONGO_URI         → cliente RW de la app (motores, jobs, API write paths)
  MONGO_URI_READ    → cliente RO de la app (API queries con secondaryPreferred)
  PARTNER_MONGO_URI → app independiente partner_api (RO sobre ACAPortfolio)

Imprime el usuario y el host de cada URI (NUNCA el password). Para auditar
sin entrar a Atlas: si dos vars tienen el mismo user, rotar uno los rota
a los dos; si son distintos, rotás cada uno por separado.

Uso:
    python -m scripts.diag_mongo_users
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


VARS = ("MONGO_URI", "MONGO_URI_READ", "PARTNER_MONGO_URI")


def _parse(uri: str) -> tuple[str, str, str]:
    """Devuelve (user, host, query) sin password ni path."""
    p = urlparse(uri)
    user = p.username or "(sin user)"
    host = p.hostname or "(sin host)"
    query = p.query or ""
    return user, host, query


def main() -> None:
    print(f"Leyendo .env: {os.path.join(_PROJECT_ROOT, '.env')}\n")
    seen: dict[str, list[str]] = {}
    for var in VARS:
        uri = os.getenv(var)
        if not uri:
            print(f"  {var:<22s}  (no seteada)")
            continue
        user, host, query = _parse(uri)
        # Mostrar solo el primer parámetro relevante (readPreference, etc.).
        flags = ""
        for k in ("readPreference", "appName"):
            for kv in query.split("&"):
                if kv.startswith(k + "="):
                    flags += f"  {kv}"
        print(f"  {var:<22s}  user={user}   host={host}{flags}")
        seen.setdefault(user, []).append(var)

    # Resumen de qué vars comparten usuario.
    print("\nUsuarios distintos en uso:")
    for user, vars_ in seen.items():
        print(f"  {user:<28s} ← {', '.join(vars_)}")


if __name__ == "__main__":
    main()
