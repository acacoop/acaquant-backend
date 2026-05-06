"""Insert masivo en Valuaciones.AuM desde un CSV.

Pensado para backfill manual de cuentas que Aunesa no responde de forma
fiable (timeouts crónicos en data histórica). El operador exporta los
snapshots a CSV y este script los inserta en Mongo respetando la clave
única (id_cuenta, unidad, fecha_snapshot).

Privacidad: el script imprime SOLO conteos agregados — nunca filas
individuales — así el output puede compartirse sin exponer importes.

Default: skip-if-exists. Si una fila del CSV tiene una clave que ya
existe en Mongo, NO la pisa. Si querés sobreescribir data vieja, pasá
--overwrite.

Formato esperado del CSV (UTF-8, headers en la primera línea):
    id_cuenta,unidad,fecha_snapshot,cantidad,cuenta,precio,timestamp,tipoTitulo,valuacion

`timestamp` y `tipoTitulo` pueden venir vacíos — se completan con default
seguros: timestamp = "<fecha_snapshot>T23:00:00 UTC", tipoTitulo = "".

Uso:
    python -m scripts.insert_aum_csv docs/aum_255_all_snapshots.csv --dry-run
    python -m scripts.insert_aum_csv docs/aum_255_all_snapshots.csv
    python -m scripts.insert_aum_csv docs/aum_255_all_snapshots.csv --overwrite
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pymongo import UpdateOne

from core.mongo import get_mongo_client


def _to_float(v: str | None) -> float | None:
    if v is None or v == "" or v.strip().lower() == "none":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_timestamp(raw: str | None, fecha_snapshot: str) -> datetime:
    """Si el CSV trae timestamp ISO, lo parseamos. Si viene vacío, sintetizamos
    `<fecha_snapshot>T23:00:00 UTC` (mismo patrón que jobs/aum_backfill.py)."""
    if raw and raw.strip():
        try:
            return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.fromisoformat(f"{fecha_snapshot}T23:00:00").replace(tzinfo=UTC)


def _row_to_doc(row: dict) -> dict:
    fecha_snapshot = row["fecha_snapshot"].strip()
    return {
        "id_cuenta":      str(row["id_cuenta"]).strip(),
        "unidad":         row["unidad"].strip(),
        "fecha_snapshot": fecha_snapshot,
        "cuenta":         row.get("cuenta", "").strip(),
        "cantidad":       _to_float(row.get("cantidad")),
        "precio":         _to_float(row.get("precio")),
        "valuacion":      _to_float(row.get("valuacion")),
        "tipoTitulo":     (row.get("tipoTitulo") or "").strip(),
        "timestamp":      _to_timestamp(row.get("timestamp"), fecha_snapshot),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", help="Path al CSV a insertar")
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview — no escribe en Mongo. Muestra conteos por fecha.")
    ap.add_argument("--overwrite", action="store_true",
                    help="Sobrescribe filas cuya (id_cuenta,unidad,fecha_snapshot) "
                         "ya existe en Mongo. Default: skip-if-exists.")
    args = ap.parse_args()

    path = Path(args.csv_path)
    if not path.exists():
        print(f"❌ No existe: {path}")
        sys.exit(1)

    # ── 1. Leer CSV → docs en memoria ─────────────────────────────────
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"id_cuenta", "unidad", "fecha_snapshot"}
        if not required.issubset(reader.fieldnames or []):
            print(f"❌ Faltan columnas requeridas: {required - set(reader.fieldnames or [])}")
            sys.exit(1)
        for r in reader:
            try:
                rows.append(_row_to_doc(r))
            except (KeyError, ValueError) as e:
                print(f"⚠ fila {reader.line_num}: skip ({type(e).__name__})")

    print(f"Leídas {len(rows)} filas del CSV.")
    if not rows:
        return

    # ── 2. Chequear cuáles ya existen en Mongo (en chunks de 500) ─────
    client = get_mongo_client()
    col = client["Valuaciones"]["AuM"]
    existing: set[tuple[str, str, str]] = set()
    chunk_size = 500
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        or_clauses = [
            {
                "id_cuenta":      r["id_cuenta"],
                "unidad":         r["unidad"],
                "fecha_snapshot": r["fecha_snapshot"],
            }
            for r in chunk
        ]
        for d in col.find(
            {"$or": or_clauses},
            {"_id": 0, "id_cuenta": 1, "unidad": 1, "fecha_snapshot": 1},
        ):
            existing.add((d["id_cuenta"], d["unidad"], d["fecha_snapshot"]))

    nuevas = [r for r in rows if (r["id_cuenta"], r["unidad"], r["fecha_snapshot"]) not in existing]
    pisadas = [r for r in rows if (r["id_cuenta"], r["unidad"], r["fecha_snapshot"]) in existing]
    print(f"  ya existen: {len(pisadas)}  (skip por defecto, --overwrite para pisar)")
    print(f"  nuevas:     {len(nuevas)}")

    fechas_new = Counter(r["fecha_snapshot"] for r in nuevas)
    fechas_pi  = Counter(r["fecha_snapshot"] for r in pisadas)
    print("\nDistribución por fecha (nuevas | ya existen):")
    todas_fechas = sorted(set(fechas_new) | set(fechas_pi))
    for f in todas_fechas:
        print(f"  {f}: {fechas_new.get(f, 0):4d} | {fechas_pi.get(f, 0):4d}")

    # ── 3. Decidir qué escribir ───────────────────────────────────────
    if args.overwrite:
        to_write = rows  # tanto nuevas como las que ya existen (se pisan)
    else:
        to_write = nuevas

    if args.dry_run:
        print(f"\n--dry-run: {len(to_write)} filas se escribirían. Nada hecho.")
        return

    if not to_write:
        print("\nNada para escribir.")
        return

    # ── 4. bulk_write ─────────────────────────────────────────────────
    print(f"\nEscribiendo {len(to_write)} filas a Valuaciones.AuM…")
    ops = [
        UpdateOne(
            {
                "id_cuenta":      r["id_cuenta"],
                "unidad":         r["unidad"],
                "fecha_snapshot": r["fecha_snapshot"],
            },
            {"$set": r},
            upsert=True,
        )
        for r in to_write
    ]
    result = col.bulk_write(ops, ordered=False)
    print(f"✅ upserted={result.upserted_count}  matched={result.matched_count}  "
          f"modified={result.modified_count}")

    # ── 5. Refrescar el rollup FCI para cada fecha tocada ─────────────
    fechas_tocadas = sorted({r["fecha_snapshot"] for r in to_write})
    if fechas_tocadas:
        print(f"\nRefrescando AuMResumenFCI para {len(fechas_tocadas)} fechas…")
        from jobs.aum_resumen_fci import sync_fecha as sync_resumen_fci
        for f in fechas_tocadas:
            sync_resumen_fci(client, f)
        print("✅ Rollup FCI actualizado.")


if __name__ == "__main__":
    main()
