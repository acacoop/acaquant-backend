"""scripts/watch_db.py — feed EN VIVO de todo lo que se ESCRIBE en Mongo.

La visibilidad que faltaba: abre un change stream del cluster e imprime cada
insert/update/replace/delete a medida que pasa, con el namespace (db.colección),
el tipo de op y los campos clave. Filtrable por db / colección / tipo de op.

POR QUÉ ES LIVIANO (y esta vez lo afirmo midiendo, no asumiendo): un change
stream es un cursor tailable sobre el OPLOG (el log de replicación que Mongo ya
escribe igual). NO escanea colecciones, NO corre queries sobre los datos → no
agrega carga de lectura al cluster. Es el mecanismo estándar de CDC de MongoDB.

NO setea `full_document=updateLookup` a propósito: eso haría un FETCH extra del
doc por cada update (= carga). Los inserts ya traen el doc gratis; los updates
muestran solo los campos que cambiaron (gratis, vienen en el evento).

Uso (en una terminal del Droplet, dejalo corriendo; Ctrl+C para cortar):
    python -m scripts.watch_db                      # todo
    python -m scripts.watch_db --db CashFlow        # solo una base
    python -m scripts.watch_db --col Operaciones    # solo una colección
    python -m scripts.watch_db --op insert,update   # solo ciertas ops
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from core.mongo import get_mongo_client


def _resumen(ch: dict) -> str:
    """Una línea compacta del cambio (sin fetches extra)."""
    op = ch.get("operationType", "?")
    if op == "insert":
        doc = ch.get("fullDocument") or {}  # los inserts traen el doc gratis
        claves = [k for k in ("boleto", "comprobante", "concertacion", "fecha",
                              "id_cuenta", "cuenta", "ticker", "categoria", "operacion")
                  if k in doc]
        det = " · ".join(f"{k}={doc[k]}" for k in claves[:4])
        return det or f"campos: {', '.join(list(doc)[:5])}"
    if op in ("update", "replace"):
        upd = (ch.get("updateDescription") or {}).get("updatedFields") or {}
        return "campos: " + ", ".join(list(upd)[:8]) if upd else ""
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="filtrar por base (ej. CashFlow)")
    ap.add_argument("--col", help="filtrar por colección (ej. Operaciones)")
    ap.add_argument("--op", help="ops separadas por coma: insert,update,replace,delete")
    args = ap.parse_args()

    match: dict = {}
    if args.op:
        match["operationType"] = {"$in": [o.strip() for o in args.op.split(",") if o.strip()]}
    if args.db:
        match["ns.db"] = args.db
    if args.col:
        match["ns.coll"] = args.col
    pipeline = [{"$match": match}] if match else []

    filtro = " · ".join(f"{k}={v}" for k, v in
                        (("db", args.db), ("col", args.col), ("op", args.op)) if v)
    print(f"▶ Escuchando escrituras del cluster{(' [' + filtro + ']') if filtro else ''}… "
          "(Ctrl+C para cortar)\n")
    print(f"  {'hora':8} {'op':9} {'colección':34} detalle")

    client = get_mongo_client()
    n = 0
    with client.watch(pipeline) as stream:
        for ch in stream:
            ns = ch.get("ns") or {}
            coll = f"{ns.get('db')}.{ns.get('coll')}"
            ts = datetime.now(UTC).strftime("%H:%M:%S")
            print(f"  {ts:8} {ch.get('operationType', '?'):9} {coll:34} {_resumen(ch)}")
            n += 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n— cortado —")
