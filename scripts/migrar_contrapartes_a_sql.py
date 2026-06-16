"""scripts/migrar_contrapartes_a_sql.py — deja `clientes.contrapartes` (SQL) como
fuente única, copiando TODO lo que tenía Mongo `CashFlow.Contrapartes` una última vez.

Qué hace (idempotente):
  1. ALTER clientes.contrapartes ADD COLUMN denominacion (faltaba; Mongo la tenía y la
     usa el panel para cuentas que no son comitentes).
  2. Copia Mongo → SQL: id_cuenta(=cuenta), contraparte, segmento, denominacion, origen.
     UPSERT por id_cuenta. Después de esto SQL = Mongo.

Tras correrlo: el editor del panel, _aum_filters, flujo y el sync ya leen/escriben SQL
(código en el mismo deploy). Mongo CashFlow.Contrapartes queda para dropear.

    python -m scripts.migrar_contrapartes_a_sql            # PREVIEW (cuenta, no escribe)
    python -m scripts.migrar_contrapartes_a_sql --commit   # aplica
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client_read
from core.postgres import get_pool


def main() -> None:
    commit = "--commit" in sys.argv
    docs = list(get_mongo_client_read()["CashFlow"]["Contrapartes"].find(
        {}, {"_id": 0, "cuenta": 1, "contraparte": 1, "segmento": 1, "denominacion": 1, "origen": 1}))
    rows = []
    for d in docs:
        idc = str(d.get("cuenta")).strip() if d.get("cuenta") is not None else ""
        if idc:
            rows.append((idc, _s(d.get("denominacion")), _s(d.get("contraparte")),
                         _s(d.get("segmento")), _s(d.get("origen"))))

    print("\n=== Mongo CashFlow.Contrapartes → clientes.contrapartes ===")
    print(f"   docs en Mongo: {len(docs)}  ·  con cuenta válida: {len(rows)}")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM contrapartes")
        print(f"   filas en SQL clientes.contrapartes (antes): {cur.fetchone()[0]}")
        if not commit:
            print("\n[PREVIEW] no se escribió nada. Repetí con --commit.\n")
            return
        cur.execute("ALTER TABLE clientes.contrapartes ADD COLUMN IF NOT EXISTS denominacion text")
        cur.executemany(
            "INSERT INTO contrapartes (id_cuenta, denominacion, contraparte, segmento, origen) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (id_cuenta) DO UPDATE SET "
            "denominacion = COALESCE(EXCLUDED.denominacion, contrapartes.denominacion), "
            "contraparte = EXCLUDED.contraparte, segmento = EXCLUDED.segmento, "
            "origen = COALESCE(EXCLUDED.origen, contrapartes.origen)", rows)
        conn.commit()
        cur.execute("SELECT count(*) FROM contrapartes")
        print(f"   filas en SQL (después): {cur.fetchone()[0]}")
        print("\n[COMMIT] listo. git pull + restart api → todo lee/escribe SQL.\n")


def _s(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


if __name__ == "__main__":
    main()
