"""Ciclo de vida de los checkpoints internos de LangGraph."""
from __future__ import annotations

from collections.abc import Iterable

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo

from core.postgres import get_postgres_uri


def dsn() -> str:
    return make_conninfo(get_postgres_uri(), options="-c search_path=ia")


def borrar_threads(run_ids: Iterable[str]) -> int:
    """Borra checkpoints, blobs y writes mediante la API pública del saver."""
    ids = list(dict.fromkeys(str(run_id) for run_id in run_ids if run_id))
    if not ids:
        return 0
    with PostgresSaver.from_conn_string(dsn()) as saver:
        for run_id in ids:
            saver.delete_thread(run_id)
    return len(ids)