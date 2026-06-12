"""scripts/diag_inventario_mongo_sql.py — foto FACTUAL Mongo vs SQL (análisis de migración).

Read-only. NO asume nada de docs: se conecta a las dos bases reales y lista TODO
lo que hay, con conteos. Es la prueba dura del estado de la migración Mongo→SQL.

- Mongo: recorre TODAS las bases del proyecto y TODAS sus colecciones, con
  `estimated_document_count()` (usa metadata, NO escanea → barato, seguro en prod).
- Postgres: lista TODAS las tablas con su conteo estimado (`pg_class.reltuples`,
  tampoco escanea).

Salida: dos inventarios + una tabla cruzada con un mapeo heurístico
colección↔tabla (por nombre) para ver de un vistazo qué colección NO tiene
espejo en SQL.

    python -m scripts.diag_inventario_mongo_sql
"""
from __future__ import annotations

import re

from core.mongo import get_mongo_client_read

# Bases del sistema (CLAUDE.md). list_database_names() igual las descubre solas,
# pero filtramos las internas de Mongo para no ensuciar.
_DBS_INTERNAS = {"admin", "local", "config"}


def _norm(s: str) -> str:
    """Normaliza nombre para matchear colección↔tabla: minúsculas, sin guiones/_."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def inventario_mongo() -> dict[str, dict[str, int]]:
    cli = get_mongo_client_read()
    out: dict[str, dict[str, int]] = {}
    for dbname in sorted(cli.list_database_names()):
        if dbname in _DBS_INTERNAS:
            continue
        db = cli[dbname]
        cols: dict[str, int] = {}
        for c in sorted(db.list_collection_names()):
            try:
                cols[c] = db[c].estimated_document_count()
            except Exception as e:
                cols[c] = -1
                print(f"  ! {dbname}.{c}: {str(e).splitlines()[0][:80]}")
        out[dbname] = cols
    return out


def inventario_sql() -> dict[str, int]:
    """Tablas públicas de Postgres con conteo estimado (sin escanear)."""
    try:
        from core.postgres import get_pool
    except Exception as e:
        print(f"\n[SQL] no se pudo importar core.postgres: {e}")
        return {}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT relname, reltuples::bigint
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r' AND n.nspname = 'public'
                ORDER BY relname
            """)
            return {r[0]: int(r[1]) for r in cur.fetchall()}
    except Exception as e:
        print(f"\n[SQL] no se pudo conectar a Postgres: {str(e).splitlines()[0][:120]}")
        return {}


def main() -> int:
    print("=" * 70)
    print("INVENTARIO MONGO (todas las bases / colecciones / conteo estimado)")
    print("=" * 70)
    mongo = inventario_mongo()
    total_cols = 0
    for dbname, cols in mongo.items():
        print(f"\n### {dbname}  ({len(cols)} colecciones)")
        for c, n in cols.items():
            print(f"    {c:<32} {n:>12,}")
            total_cols += 1

    print("\n" + "=" * 70)
    print("INVENTARIO SQL (tablas públicas / conteo estimado)")
    print("=" * 70)
    sql = inventario_sql()
    print(f"\n### postgres  ({len(sql)} tablas)")
    for t, n in sql.items():
        print(f"    {t:<32} {n:>12,}")

    # Mapeo heurístico por nombre: qué colección Mongo tiene una tabla parecida.
    print("\n" + "=" * 70)
    print("CRUCE colección Mongo  →  ¿tabla SQL con nombre parecido?")
    print("(heurístico por nombre; ❌ = sin espejo evidente en SQL)")
    print("=" * 70)
    sql_norm = {_norm(t): t for t in sql}
    sin_espejo: list[str] = []
    for dbname, cols in mongo.items():
        for c in cols:
            n = _norm(c)
            # match exacto o por contención en cualquier sentido
            hit = sql_norm.get(n)
            if not hit:
                for sn, st in sql_norm.items():
                    if n and (n in sn or sn in n):
                        hit = st
                        break
            etiqueta = f"→ {hit}" if hit else "❌ SIN ESPEJO SQL"
            if not hit:
                sin_espejo.append(f"{dbname}.{c}")
            print(f"    {dbname + '.' + c:<44} {etiqueta}")

    print("\n" + "=" * 70)
    print(f"RESUMEN: {total_cols} colecciones Mongo | {len(sql)} tablas SQL | "
          f"{len(sin_espejo)} colecciones SIN espejo SQL evidente")
    print("=" * 70)
    if sin_espejo:
        print("\nColecciones Mongo sin tabla SQL parecida (candidatas a 'quedó afuera'):")
        for s in sin_espejo:
            print(f"    - {s}")
    print("\nNOTA: el cruce es por NOMBRE. Que una colección no tenga tabla puede ser")
    print("a propósito (no se migra) o un olvido. Cruzar con el mapa de USO del código")
    print("para decidir cuáles importan de verdad.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
