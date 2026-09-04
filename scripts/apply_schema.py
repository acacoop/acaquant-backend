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

# ⚠️ **EL LAB VA DESPUÉS, Y SUS ERRORES NO CUENTAN.** `sql/lab.sql` trae el
# esquema del INVESTIGADOR y, sobre todo, el ALCANCE de sus dos roles sobre
# producción (revoca y vuelve a otorgar). Va acá y no pegado a mano porque
# antes se aplicaba copiando texto al editor de Supabase: media DDL del lab se
# autocuraba en cada deploy (los GRANT de las vistas, que viven en schema.sql)
# y la otra media dependía de que alguien se acordara. Un `ALTER` nuevo andaba
# local y en producción no existía, con un error que hablaba de otra cosa.
#
# Va DESPUÉS porque otorga permisos sobre tablas que `schema.sql` crea, y sus
# fallos se reportan aparte SIN cambiar el código de salida: **el lab es
# opcional, la mesa no**. Que el deploy de toda la plataforma quede en rojo por
# una herramienta interna sería el mismo error que ya está prohibido del otro
# lado (el agente sobrevive a un lab roto).
LAB_PATH = Path(__file__).resolve().parent.parent / "sql" / "lab.sql"


def _statements(sql: str) -> list[str]:
    """Parte el script en statements respetando bloques dollar-quoted ($$ ... $$,
    $tag$ ... $tag$) — schema.sql tiene bloques DO $$ ... $$ (PL/pgSQL) con ';'
    internos que NO son separadores. Quita comentarios '--' y splitea por ';'
    solo FUERA de un bloque dollar-quoted."""
    sin_comentarios = "\n".join(
        re.sub(r"--.*$", "", line) for line in sql.splitlines()
    )
    out: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sin_comentarios)
    dollar_tag: str | None = None  # tag actual abierto ($$ o $foo$), o None
    while i < n:
        ch = sin_comentarios[i]
        if ch == "$":
            m = re.match(r"\$[A-Za-z_0-9]*\$", sin_comentarios[i:])
            if m:
                tag = m.group(0)
                if dollar_tag is None:
                    dollar_tag = tag        # abre bloque
                elif dollar_tag == tag:
                    dollar_tag = None       # cierra bloque
                buf.append(tag)
                i += len(tag)
                continue
        if ch == ";" and dollar_tag is None:
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    resto = "".join(buf).strip()
    if resto:
        out.append(resto)
    return out


def _aplicar(cur, stmts: list[str]) -> tuple[int, list[str]]:
    """Ejecuta los statements y devuelve (cuántos anduvieron, los que fallaron)."""
    ok, fallidos = 0, []
    for s in stmts:
        try:
            cur.execute(s)
            ok += 1
        except Exception as e:  # idempotente: la mayoría de "ya existe" ni llega acá
            fallidos.append(f"{s.splitlines()[0][:80]} → {type(e).__name__}: {e}")
    return ok, fallidos


def main(dry: bool) -> int:
    # encoding EXPLÍCITO: schema.sql tiene comentarios con acentos y el default
    # del sistema no es UTF-8 en todos lados (en Windows revienta con
    # UnicodeDecodeError y no se puede ni validar el archivo antes de subirlo).
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    stmts = _statements(sql)
    lab = _statements(LAB_PATH.read_text(encoding="utf-8")) if LAB_PATH.exists() else []
    print(f"schema.sql: {len(stmts)} statements   ·   lab.sql: {len(lab)} statements\n")

    if dry:
        for s in stmts + lab:
            primera = s.splitlines()[0][:90]
            print(f"  {primera}")
        return 0

    # Autocommit: cada DDL se confirma sola; un statement que falle no aborta el resto.
    conn = connect()
    conn.autocommit = True
    with conn.cursor() as cur:
        ok, fallidos = _aplicar(cur, stmts)
        ok_lab, fallidos_lab = _aplicar(cur, lab)
    conn.close()

    print(f"OK: {ok}   Errores: {len(fallidos)}")
    if fallidos:
        print("\nStatements con error (revisar — pueden ser inofensivos):")
        for f in fallidos:
            print(f"  ✗ {f}")
    else:
        print("\nTodo aplicado. Las tablas faltantes (dias_habiles, dolar, series_macro, "
              "portfolio_snapshot, operaciones.*, etc.) ahora existen.")

    if lab:
        print(f"\nlab.sql (el INVESTIGADOR): OK {ok_lab}   Errores: {len(fallidos_lab)}")
        for f in fallidos_lab:
            print(f"  ⚠ {f}")
        if fallidos_lab:
            # AVISA y no cuenta: el lab es opcional, la mesa no. Si el usuario
            # del deploy no puede otorgar, o los roles no existen todavía, eso
            # NO puede dejar en rojo el deploy de toda la plataforma.
            print("  ↳ el lab quedó a medias. La mesa NO está afectada.")
            print("  ↳ para ver qué falta: python -m lab.langgraph.probar_lector")

    # El código de salida lo define SOLO schema.sql (ver el comentario de LAB_PATH).
    return 1 if fallidos else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="solo listar, no ejecutar")
    raise SystemExit(main(ap.parse_args().dry_run))
