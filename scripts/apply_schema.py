"""Aplica sql/schema.sql a la base Postgres real — idempotente y NO destructivo.

EL PROBLEMA QUE RESUELVE: `schema.sql` NO se aplica solo. `sync_postgres` asume
que las tablas existen. Resultado: tablas que están en el archivo pero NUNCA se
crearon en la DB (ej. mercado.dias_habiles, valuaciones.dolar, macro.series_macro)
→ los dual-writes fallan con "relation does not exist" y la migración Mongo→SQL
queda trabada. Esto las crea TODAS de una.

SEGURO de correr cuantas veces quieras: schema.sql es 100% idempotente —
CREATE SCHEMA/TABLE/INDEX IF NOT EXISTS + ALTER TABLE ADD COLUMN IF NOT EXISTS,
sin un solo DROP/DELETE/TRUNCATE. Lo que ya existe se saltea; solo crea lo que
falta.

Uso (Droplet, raíz):
    python -m scripts.apply_schema            # aplica
    python -m scripts.apply_schema --dry-run  # solo lista los statements

REGLA #4: crear un índice nuevo sobre una tabla grande puede tardar/lockear.
Las tablas SQL son espejos chicos, pero igual: correlo FUERA de rueda (no 13-20
UTC L-V) por las dudas.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from core.postgres import connect

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def _statements(sql: str) -> list[str]:
    """Parte el script en statements. schema.sql es DDL pura (sin funciones ni
    dollar-quoting), así que quitar comentarios '--' y splitear por ';' es seguro."""
    sin_comentarios = "\n".join(
        re.sub(r"--.*$", "", line) for line in sql.splitlines()
    )
    return [s.strip() for s in sin_comentarios.split(";") if s.strip()]


def main(dry: bool) -> int:
    sql = SCHEMA_PATH.read_text()
    stmts = _statements(sql)
    print(f"schema.sql: {len(stmts)} statements\n")

    if dry:
        for s in stmts:
            primera = s.splitlines()[0][:90]
            print(f"  {primera}")
        return 0

    ok = errores = 0
    fallidos: list[str] = []
    # Autocommit: cada DDL se confirma sola; un statement que falle no aborta el resto.
    conn = connect()
    conn.autocommit = True
    with conn.cursor() as cur:
        for s in stmts:
            try:
                cur.execute(s)
                ok += 1
            except Exception as e:  # idempotente: la mayoría de "ya existe" ni llega acá
                errores += 1
                fallidos.append(f"{s.splitlines()[0][:80]} → {type(e).__name__}: {e}")
    conn.close()

    print(f"OK: {ok}   Errores: {errores}")
    if fallidos:
        print("\nStatements con error (revisar — pueden ser inofensivos):")
        for f in fallidos:
            print(f"  ✗ {f}")
    else:
        print("\nTodo aplicado. Las tablas faltantes (dias_habiles, dolar, series_macro, "
              "portfolio_snapshot, operaciones.*, etc.) ahora existen.")
    return 1 if errores else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="solo listar, no ejecutar")
    raise SystemExit(main(ap.parse_args().dry_run))
