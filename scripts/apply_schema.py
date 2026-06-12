"""scripts/apply_schema.py — aplica sql/schema.sql a Postgres (idempotente).

Crea las tablas/columnas/índices que falten (todo el schema usa CREATE/ALTER ...
IF NOT EXISTS). Se corre tras tocar `sql/schema.sql` — ej. la columna nueva
`comitentes.referido` y las tablas `market_quotes` / `market_calendar`, que sin
esto hacían fallar el sync (la tabla/columna no existía).

Idempotente: re-correrlo no rompe nada (lo que ya existe se saltea).

Uso (en el Droplet):
    python -m scripts.apply_schema
"""
from __future__ import annotations

import re
from pathlib import Path

from core.postgres import connect


def main() -> int:
    sql = Path("sql/schema.sql").read_text(encoding="utf-8")
    # Quita comentarios de línea ANTES de splitear (evita que un ';' dentro de un
    # comentario rompa el split).
    sin_comentarios = re.sub(r"--[^\n]*", "", sql)
    # Split por ';' RESPETANDO bloques dollar-quoted ($$ ... $$): schema.sql tiene
    # algún `DO $$ ... END $$` (migración idempotente) cuyos ';' internos NO son
    # separadores — partirlo genera pedazos inválidos (SyntaxError).
    stmts: list[str] = []
    buf: list[str] = []
    in_dollar = False
    i = 0
    while i < len(sin_comentarios):
        if sin_comentarios[i:i + 2] == "$$":
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        ch = sin_comentarios[i]
        if ch == ";" and not in_dollar:
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    print(f"Aplicando sql/schema.sql → {len(stmts)} statements…\n")

    ok, fail = 0, 0
    with connect() as conn:
        for st in stmts:
            try:
                with conn.cursor() as cur:
                    cur.execute(st)
                conn.commit()
                ok += 1
            except Exception as e:
                conn.rollback()
                fail += 1
                head = " ".join(st.split())[:70]
                print(f"  ⚠ {head}… → {type(e).__name__}: {str(e).splitlines()[0][:120]}")

    print(f"\nLISTO: {ok} OK, {fail} con error.")
    if fail:
        print("(los errores suelen ser inofensivos si la tabla/columna ya existía)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
