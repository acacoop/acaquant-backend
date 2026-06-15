"""scripts/diff_assets_mongo_sql.py — READ-ONLY. Compara el catálogo de títulos
Mongo `Valuaciones.Assets` vs SQL `portafolio.assets`, unidad por unidad.

Sirve para confirmar que SQL ya es espejo fiel de Mongo antes de deprecar Mongo
(fuente de verdad pasa a SQL). Reporta:
  - unidades solo en Mongo / solo en SQL
  - unidades con algún campo distinto (cartera, clase, emisor, ticker, etc.)

    python -m scripts.diff_assets_mongo_sql            # resumen + primeras 25 difs
    python -m scripts.diff_assets_mongo_sql --all      # lista TODAS las difs

No escribe nada.
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client_read
from core.postgres import get_pool

# (campo Mongo UPPERCASE, columna SQL)
_CAMPOS = [
    ("CARTERA", "cartera"), ("CLASE_ACTIVO", "clase_activo"), ("EMISOR", "emisor"),
    ("TICKER", "ticker"), ("INSTRUMENTO", "instrumento"), ("CALIFICACION", "calificacion"),
    ("CAFCI", "cafci"), ("VENCIMIENTO", "vencimiento"), ("CODIGO_CNV", "codigo_cnv"),
    ("FEE_ADMIN", "fee_admin"),
]


def _norm(v) -> str:
    """Normaliza para comparar: None / '' / 'NO APLICA' → '' ; números → str limpio."""
    if v is None:
        return ""
    s = str(v).strip()
    if s.upper() == "NO APLICA":
        return ""
    # 1.0 (SQL numeric) vs 1 (Mongo int) → comparar como float si se puede
    try:
        f = float(s)
        return repr(round(f, 10))
    except ValueError:
        return s


def main() -> None:
    mostrar_todo = "--all" in sys.argv

    mongo = {}
    proj = {"_id": 0, "unidad": 1, **{c: 1 for c, _ in _CAMPOS}}
    for d in get_mongo_client_read()["Valuaciones"]["Assets"].find({}, proj):
        u = (d.get("unidad") or "").strip()
        if u:
            mongo[u] = {c: d.get(c) for c, _ in _CAMPOS}

    sql = {}
    sqlcols = ", ".join(col for _, col in _CAMPOS)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT unidad, {sqlcols} FROM portafolio.assets")
        for row in cur.fetchall():
            u = (row[0] or "").strip()
            if u:
                sql[u] = {c: row[i + 1] for i, (c, _) in enumerate(_CAMPOS)}

    solo_mongo = sorted(set(mongo) - set(sql))
    solo_sql = sorted(set(sql) - set(mongo))
    difs = []
    for u in sorted(set(mongo) & set(sql)):
        campos_dif = [c for c, _ in _CAMPOS if _norm(mongo[u][c]) != _norm(sql[u][c])]
        if campos_dif:
            difs.append((u, campos_dif))

    print("\n=== DIFF assets · Mongo Valuaciones.Assets vs SQL portafolio.assets ===")
    print(f"   Mongo: {len(mongo)} unidades   ·   SQL: {len(sql)} unidades")
    print(f"   solo en MONGO : {len(solo_mongo)}")
    print(f"   solo en SQL   : {len(solo_sql)}")
    print(f"   con difs       : {len(difs)}")

    if solo_mongo:
        print(f"\n  Solo en MONGO (faltan en SQL): {solo_mongo[:25]}"
              + (" …" if len(solo_mongo) > 25 else ""))
    if solo_sql:
        print(f"\n  Solo en SQL (faltan en Mongo): {solo_sql[:25]}"
              + (" …" if len(solo_sql) > 25 else ""))

    if difs:
        print(f"\n  Unidades con campos distintos ({'todas' if mostrar_todo else 'primeras 25'}):")
        for u, campos in (difs if mostrar_todo else difs[:25]):
            detalle = "; ".join(
                f"{c}: mongo={mongo[u][c]!r} sql={sql[u][c]!r}" for c in campos)
            print(f"    {u:<26} {detalle}")

    if not solo_mongo and not solo_sql and not difs:
        print("\n  ✓ Mongo y SQL están IDÉNTICOS — se puede deprecar Mongo Assets.\n")


if __name__ == "__main__":
    main()
