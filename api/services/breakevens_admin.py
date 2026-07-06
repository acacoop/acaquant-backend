"""api/services/breakevens_admin.py — curaduría de pares de breakevens.

El motor `engines/breakevens.py::cargar_pares` empareja cada Lecap/Boncap con el
CER de vto más cercano (±20 días) de forma AUTOMÁTICA. A veces empareja mal (ej.
S13N6 ↔ TX26 → BE mensual −35%), y hasta ahora no había forma de corregirlo salvo
tocar código.

Este módulo permite EXCLUIR pares desde Manager. Los excluidos se guardan en
`mercado.breakevens_overrides` y el reader (`mercado_hist_sql.get_breakevens`) los
FILTRA al leer — sin tocar el motor, con efecto instantáneo (el motor sigue
calculándolos, pero la vista no los muestra).

Slice 1 = solo excluir. Forzar un par manual (Lecap↔CER elegido a mano) es un
paso posterior.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

_TABLE = "mercado.breakevens_overrides"


def _ensure(cur) -> None:
    """Self-create de la tabla de overrides (tolera drift de schema)."""
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
        "lecap text NOT NULL, cer text NOT NULL, "
        "updated_by text, updated_at timestamptz, "
        "PRIMARY KEY (lecap, cer))")


def get_excluidos() -> set[tuple[str, str]]:
    """Set de pares (lecap, cer) excluidos — claves = tickers CORTOS (los que trae
    el doc del motor: S13N6, TX26…). set() ante error (fail-open: preferimos mostrar
    de más y no romper la vista de breakevens)."""
    from psycopg.rows import dict_row

    from core.postgres import get_pool
    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            _ensure(cur)
            cur.execute(f"SELECT lecap, cer FROM {_TABLE}")
            rows = cur.fetchall()
            conn.commit()
        return {(r["lecap"], r["cer"]) for r in rows}
    except Exception:
        return set()


def set_exclusion(lecap: str, cer: str, excluir: bool, email: str) -> dict[str, Any]:
    """Excluir (excluir=True) o reincluir (False) el par (lecap, cer). Idempotente."""
    if not lecap or not cer:
        raise ValueError("lecap y cer son obligatorios")

    from core.postgres import get_pool
    now = datetime.now(UTC)
    with get_pool().connection() as conn, conn.cursor() as cur:
        _ensure(cur)
        if excluir:
            cur.execute(
                f"INSERT INTO {_TABLE} (lecap, cer, updated_by, updated_at) "
                f"VALUES (%s, %s, %s, %s) ON CONFLICT (lecap, cer) "
                f"DO UPDATE SET updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at",
                (lecap, cer, email, now))
        else:
            cur.execute(f"DELETE FROM {_TABLE} WHERE lecap = %s AND cer = %s", (lecap, cer))
        conn.commit()
    return {"lecap": lecap, "cer": cer, "excluido": excluir}


def list_pares_con_estado() -> dict[str, Any]:
    """Pares VIVOS del motor + flag `excluido`, para la matriz de Manager. Muestra
    TODOS (incluidos los excluidos) para poder reincluirlos. Lee el doc crudo (sin
    filtrar) del reader de breakevens."""
    from api.services.mercado_hist_sql import breakevens_docs_raw
    docs = breakevens_docs_raw()
    pares = (docs[0].get("pares") if docs else None) or []
    excl = get_excluidos()
    out = [{**p, "excluido": (p.get("lecap"), p.get("cer")) in excl} for p in pares]
    return {
        "updated_at":  docs[0].get("updated_at") if docs else None,
        "pares":       out,
        "n":           len(out),
        "n_excluidos": len(excl),
    }
