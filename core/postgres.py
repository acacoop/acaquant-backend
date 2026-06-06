"""core/postgres.py — conexión a Postgres (Supabase), capa relacional analítica.

Espejo de SOLO LECTURA del núcleo relacional (ver docs/SQL.md y ARQUITECTURA.md §5).
NO es la base operativa — la mesa sigue contra Mongo. core/ no importa nada del
proyecto (regla de capas) → solo os + psycopg.

Env var: POSTGRES_URI (connection string de Supabase; va en el .env, NUNCA en el repo).
"""
from __future__ import annotations

import os

import psycopg
from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

POSTGRES_URI = os.getenv("POSTGRES_URI")


def get_postgres_uri() -> str:
    if not POSTGRES_URI:
        raise RuntimeError(
            "Falta POSTGRES_URI en el .env (connection string de Supabase: "
            "Settings → Database → Connection string)."
        )
    return POSTGRES_URI


def connect() -> psycopg.Connection:
    """Nueva conexión psycopg. El caller la cierra — usar como context manager:
    `with connect() as conn: ...`. Para el sync/smoke (batch) alcanza una conexión
    por corrida; el pool para la API (lecturas concurrentes) se agrega en Fase C."""
    return psycopg.connect(get_postgres_uri())
