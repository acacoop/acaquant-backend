"""core/postgres.py — conexión a Postgres (Supabase), capa relacional analítica.

ES la base operativa y la ÚNICA del sistema: motores, jobs y API leen y
escriben acá (ver docs/SQL.md y docs/ACAQUANT.md §5). core/ no importa nada del
proyecto (regla de capas) → solo os + psycopg.

Env var: POSTGRES_URI (connection string de Supabase; va en el .env, NUNCA en el repo).
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar

import psycopg
from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

POSTGRES_URI = os.getenv("POSTGRES_URI")

_pool = None
_pool_lock = threading.Lock()
_job_pool = None
_job_pool_lock = threading.Lock()

# Routing opt-in: dentro de `use_job_pool()`, get_pool() devuelve el carril de
# JOBS (aislado, timeout 30s) en vez del carril web (8s). Es para los crons que
# REUSAN services de api/ que hardcodean get_pool() (ej. pnl_totales_precompute →
# pnl_sql / portfolio_sql): así NO compiten en el carril de la web durante la rueda
# y esperan 30s en vez de abortar a los 8s. ContextVar = aislado por hilo/tarea; la
# API NUNCA lo setea → su get_pool() devuelve el pool web exactamente como hoy.
_prefer_job_pool: ContextVar[bool] = ContextVar("_prefer_job_pool", default=False)


@contextmanager
def use_job_pool():
    """Dentro del bloque, get_pool() usa el carril de jobs (aislado, 30s).

    Uso (cron que reusa un service de api/):
        with use_job_pool():
            datos = algun_service_que_usa_get_pool()
    """
    token = _prefer_job_pool.set(True)
    try:
        yield
    finally:
        _prefer_job_pool.reset(token)


def get_postgres_uri() -> str:
    if not POSTGRES_URI:
        raise RuntimeError(
            "Falta POSTGRES_URI en el .env (connection string de Supabase: "
            "Settings → Database → Connection string)."
        )
    return POSTGRES_URI


# Las tablas están organizadas por dominio en schemas (ver sql/schema.sql §v2). El
# `search_path` resuelve los nombres SIN calificar en este orden → el código existente
# sigue funcionando sin tocar cada query (los nombres son únicos entre schemas, no hay
# colisión). `public` queda al final (vacío tras la migración v2, por si algo cae ahí).
# OJO: SIN espacios — libpq parsea `-c search_path=...` separando por espacios, un
# espacio después de la coma rompe la conexión ("List syntax is invalid").
# Postgres ignora en silencio los schemas inexistentes → desplegar este cambio ANTES de
# correr el schema.sql nuevo es seguro (durante la transición resuelve por `public`).
_SEARCH_PATH = "clientes,operaciones,portafolio,mercado,macro,valuaciones,manager,home,public"


def connect() -> psycopg.Connection:
    """Nueva conexión psycopg standalone. El caller la cierra — usar como context
    manager: `with connect() as conn: ...`. Para batch (sync/smoke/scripts) alcanza
    una conexión por corrida. La API usa el pool (`get_pool`), no esto."""
    return psycopg.connect(get_postgres_uri(), options=f"-c search_path={_SEARCH_PATH}")


def get_pool():
    """Pool de conexiones para la API / WEB (lecturas concurrentes de la capa SQL).

    Singleton lazy (igual patrón que los singletons de Mongo): se abre una vez y se
    reusa — NUNCA cerrarlo por request. Usar como:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            ...
    `psycopg_pool` se importa adentro para no pagarlo en procesos batch que solo
    usan `connect()`."""
    # Opt-in al carril de jobs (use_job_pool()). La API nunca entra acá
    # (contextvar default False) → devuelve el pool web como siempre.
    if _prefer_job_pool.get():
        return get_job_pool()
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg_pool import ConnectionPool
                # Carril WEB (usuarios). max_size 20→16: se reservan 4 para el carril
                # de jobs (get_job_pool) → un job NUNCA puede starvar la web. Total
                # 16+4=20, sin sumar conexiones contra el techo de Supabase. timeout
                # 8s: si igual se satura, falla RÁPIDO (no cuelga arrastrando el pool).
                _pool = ConnectionPool(
                    get_postgres_uri(), min_size=2, max_size=16, open=True, timeout=8.0,
                    kwargs={"options": f"-c statement_timeout=15000 -c search_path={_SEARCH_PATH}"},
                )
    return _pool


def get_job_pool():
    """Pool SEPARADO para los jobs batch (cron) — carril propio.

    Existe para AISLAR: la web (`get_pool`) no se puede caer, así que los jobs
    no comparten su pool. Chico a propósito (4): cada job agarra 1 conexión un
    ratito y son pocos a la vez; los que paralelizan pegan a Aunesa, no a PG.
    Total contra Supabase = web (16) + jobs (4) = 20, igual que antes. Mismo uso
    que `get_pool()`. Tras el upgrade de Supabase se agrandan los dos carriles."""
    global _job_pool
    if _job_pool is None:
        with _job_pool_lock:
            if _job_pool is None:
                from psycopg_pool import ConnectionPool
                # timeout 30s (NO 8 como la web): un job de fondo puede esperar por
                # una conexión libre sin drama (no es user-facing) → ante contención
                # transitoria espera en vez de abortar con PoolTimeout.
                _job_pool = ConnectionPool(
                    get_postgres_uri(), min_size=1, max_size=4, open=True, timeout=30.0,
                    kwargs={"options": f"-c statement_timeout=15000 -c search_path={_SEARCH_PATH}"},
                )
    return _job_pool
