"""Conexión Postgres (Supabase) del partner_api — espejo SQL de `ACAPortfolio`.

Parte de la migración Mongo→SQL del servicio externo (para apagar Mongo). El
`partner_api` es una app SEPARADA → tiene su PROPIA conexión PG (NO importa
`core.postgres`, igual que tiene su propia conexión Mongo en `db.py`). Usa la
misma instancia Supabase vía la env var `POSTGRES_URI`.

Las tablas viven en el schema `partner` (`partner.cartera`, `partner.api_users`,
ver sql/schema.sql). El partner_api SIEMPRE las califica con el schema →
NO depende del `search_path` de la mesa. `_ensure_schema()` las auto-crea
(idempotente) en el primer uso, así el servicio arranca aunque el schema.sql
todavía no se haya corrido a mano.

Solo se usa cuando `PARTNER_SQL=1` (lectura) o `PARTNER_SQL_WRITE=1` (escritura
del job/script); con el default (Mongo) este módulo nunca se importa de hecho.
"""
from __future__ import annotations

import os
import threading

import psycopg
from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

POSTGRES_URI: str = os.getenv("POSTGRES_URI", "").strip()

_pool = None
_pool_lock = threading.Lock()
_schema_ready = False
_schema_lock = threading.Lock()

# DDL idempotente — espejo EXACTO de sql/schema.sql §PARTNER. Se ejecuta una vez
# por proceso (lazy) para que el servicio funcione sin correr el schema.sql global.
_DDL = """
CREATE SCHEMA IF NOT EXISTS partner;

CREATE TABLE IF NOT EXISTS partner.cartera (
    fecha       date NOT NULL,
    id_cuenta   text NOT NULL,
    unidad      text NOT NULL,
    cuenta      text,
    cantidad    numeric,
    precio      numeric,
    valuacion   numeric,
    exported_at timestamptz,
    PRIMARY KEY (fecha, id_cuenta, unidad)
);
CREATE INDEX IF NOT EXISTS ix_partner_cartera_cuenta_fecha
    ON partner.cartera (id_cuenta, fecha DESC);

CREATE TABLE IF NOT EXISTS partner.api_users (
    username      text PRIMARY KEY,
    password_hash text,
    enabled       boolean,
    created_at    timestamptz
);
"""


def _require_uri() -> str:
    if not POSTGRES_URI:
        raise RuntimeError(
            "Falta POSTGRES_URI en el .env (connection string de Supabase). "
            "Necesario cuando PARTNER_SQL=1 o PARTNER_SQL_WRITE=1."
        )
    return POSTGRES_URI


def get_pool():
    """Pool de conexiones (singleton lazy) para la lectura del partner_api.

    Chico a propósito (el servicio externo es de bajo tráfico). `psycopg_pool`
    se importa adentro para no pagarlo si el flag está apagado."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg_pool import ConnectionPool
                _pool = ConnectionPool(
                    _require_uri(), min_size=1, max_size=4, open=True, timeout=8.0,
                    kwargs={"options": "-c statement_timeout=15000"},
                )
    return _pool


def ensure_schema() -> None:
    """Crea schema + tablas `partner.*` si no existen (idempotente, una vez/proceso)."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with get_pool().connection() as conn:
            conn.execute(_DDL)
        _schema_ready = True


def connect() -> psycopg.Connection:
    """Conexión psycopg standalone (para el writer/script batch). El caller la
    cierra — usar como context manager. NO usa el pool (procesos batch cortos)."""
    return psycopg.connect(_require_uri())
