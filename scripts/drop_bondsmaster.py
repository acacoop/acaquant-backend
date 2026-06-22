"""drop_bondsmaster.py — Fase 3b: dropear BondsMaster (Mongo + SQL). IRREVERSIBLE.

Cierra el decommission: TODO vive en Trading.Curvas (la `curva` decide la vista; las ONs
son curva on_<sector>). BondsMaster era el maestro redundante de ONs — ya no lo lee nadie
(verificado: ons.py CRUD escribe Curvas directo, titulos_flujos/acreencias leen Curvas,
sync_bonds_master removido). Esto borra:
  - Mongo  Trading.BondsMaster
  - SQL    mercado.bonds_master  (espejo, nadie lo lee)

Dry-run por default (solo cuenta). Con --apply ejecuta los DROP.

    python -m scripts.drop_bondsmaster            # dry-run: cuántos docs/filas
    python -m scripts.drop_bondsmaster --apply    # DROPEA (irreversible)

ORDEN: primero deployar el código que saca sync_bonds_master (git pull + restart api),
después correr esto. Si se corre antes, el próximo sync_postgres (código viejo) recrea
la tabla SQL.
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client
from core.postgres import get_pool


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ejecutar los DROP (default: dry-run)")
    args = ap.parse_args()

    trd = get_mongo_client()["Trading"]
    n_mongo = trd["BondsMaster"].estimated_document_count()
    print(f"Mongo Trading.BondsMaster: {n_mongo} docs")

    with get_pool().connection() as cn, cn.cursor() as cur:
        cur.execute("SELECT to_regclass('mercado.bonds_master')")
        existe = cur.fetchone()[0] is not None
        n_sql = None
        if existe:
            cur.execute("SELECT count(*) FROM mercado.bonds_master")
            n_sql = cur.fetchone()[0]
        print(f"SQL mercado.bonds_master: {'no existe' if not existe else f'{n_sql} filas'}")

        if not args.apply:
            print("\n(DRY-RUN — nada borrado. Correr con --apply para dropear.)")
            return 0

        if existe:
            cur.execute("DROP TABLE mercado.bonds_master")
            cn.commit()
            print("✅ SQL mercado.bonds_master DROPEADA")

    trd["BondsMaster"].drop()
    print("✅ Mongo Trading.BondsMaster DROPEADA")
    print("\n🎉 BondsMaster retirado. UNA sola base: Trading.Curvas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
