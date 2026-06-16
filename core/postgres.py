"""core/postgres.py — conexión a Postgres (Supabase), capa relacional analítica.

Espejo de SOLO LECTURA del núcleo relacional (ver docs/SQL.md y ARQUITECTURA.md §5).
NO es la base operativa — la mesa sigue contra Mongo. core/ no importa nada del
proyecto (regla de capas) → solo os + psycopg.

Env var: POSTGRES_URI (connection string de Supabase; va en el .env, NUNCA en el repo).
"""
from __future__ import annotations

import os
import threading

import psycopg
from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

POSTGRES_URI = os.getenv("POSTGRES_URI")

_pool = None
_pool_lock = threading.Lock()


def get_postgres_uri() -> str:
    if not POSTGRES_URI:
        raise RuntimeError(
            "Falta POSTGRES_URI en el .env (connection string de Supabase: "
            "Settings → Database → Connection string)."
        )
    return POSTGRES_URI


# Las tablas están organizadas por dominio en schemas (clientes/operaciones/
# portafolio). El `search_path` resuelve los nombres SIN calificar en este orden →
# el código existente sigue funcionando sin tocar cada query. `public` al final
# para mercado/manager/macro.
_SEARCH_PATH = "clientes, operaciones, portafolio, public"


def connect() -> psycopg.Connection:
    """Nueva conexión psycopg standalone. El caller la cierra — usar como context
    manager: `with connect() as conn: ...`. Para batch (sync/smoke/scripts) alcanza
    una conexión por corrida. La API usa el pool (`get_pool`), no esto."""
    return psycopg.connect(get_postgres_uri(), options=f"-c search_path={_SEARCH_PATH}")


def get_pool():
    """Pool de conexiones para la API (lecturas concurrentes de la capa SQL).

    Singleton lazy (igual patrón que los singletons de Mongo): se abre una vez y se
    reusa — NUNCA cerrarlo por request. Usar como:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            ...
    `psycopg_pool` se importa adentro para no pagarlo en procesos batch que solo
    usan `connect()`."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg_pool import ConnectionPool
                _pool = ConnectionPool(
                    get_postgres_uri(), min_size=1, max_size=8, open=True,
                    kwargs={"options": f"-c statement_timeout=15000 -c search_path={_SEARCH_PATH}"},
                )
    return _pool
