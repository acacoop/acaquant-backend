"""Backfill de los campos de segmentación MANUAL de Clientes.Comitentes desde CSV.

Los campos manuales (segmento, sub_segmento, sub_sub_segmento, sucursal,
referido) arrancan en null (el sync los inicializa así y no los toca). Este
script los carga en bloque desde un CSV: actualiza SOLO esos campos, por
`id_cuenta`, sin tocar los campos auto de Aunesa y sin crear cuentas nuevas
(la cuenta ya tiene que existir en el master — correr `jobs.sync_comitentes`
primero).

CSV esperado (header en la 1ª fila; separador `,` o `;` autodetectado;
encoding UTF-8). Solo hace falta `id_cuenta` + las columnas que quieras setear:

    id_cuenta;segmento;sub_segmento;sub_sub_segmento;sucursal;referido
    805;Premium;FCI;Money Market;San Martin;Juan Perez

Celdas vacías se ignoran (no pisan con vacío). Re-correr es idempotente.

Uso:
    python -m scripts.backfill_comitentes_segmentacion /root/TradingAV/seg.csv --dry-run
    python -m scripts.backfill_comitentes_segmentacion /root/TradingAV/seg.csv
"""
from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime

from core.mongo import get_mongo_client

DB = "Clientes"
COL = "Comitentes"
MANUAL_FIELDS = (
    "nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5",
    "primer_contacto_comercial", "riesgo_la_ft", "division",
    "adc", "dma", "observaciones", "sucursal", "referido",
)

# Aliases de header → campo canónico (tolerante a mayúsculas/espacios/guiones/barras).
_ALIASES = {
    "id_cuenta": "id_cuenta", "idcuenta": "id_cuenta", "cuenta": "id_cuenta", "id": "id_cuenta",
    "nivel_1": "nivel_1", "nivel1": "nivel_1",
    "nivel_2": "nivel_2", "nivel2": "nivel_2",
    "nivel_3": "nivel_3", "nivel3": "nivel_3",
    "nivel_4": "nivel_4", "nivel4": "nivel_4",
    "nivel_5": "nivel_5", "nivel5": "nivel_5",
    "primer_contacto_comercial": "primer_contacto_comercial",
    "1er_contacto_comercial": "primer_contacto_comercial",
    "contacto_comercial": "primer_contacto_comercial",
    "riesgo_la_ft": "riesgo_la_ft", "riesgo_laft": "riesgo_la_ft", "riesgo": "riesgo_la_ft",
    "division": "division",
    "adc": "adc",
    "dma": "dma",
    "observaciones": "observaciones", "obs": "observaciones", "notas": "observaciones",
    "sucursal": "sucursal",
    "referido": "referido",
}


def _norm(h: str) -> str:
    return (h or "").strip().lower().replace("-", "_").replace(" ", "_").replace("/", "_")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", help="ruta al CSV con id_cuenta + columnas de segmentación")
    ap.add_argument("--dry-run", action="store_true", help="no escribe, solo reporta")
    args = ap.parse_args()

    with open(args.csv_path, encoding="utf-8-sig", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        rows = list(reader)
        headers = reader.fieldnames or []

    colmap = {h: _ALIASES[_norm(h)] for h in headers if _norm(h) in _ALIASES}
    if "id_cuenta" not in colmap.values():
        print(f"❌ El CSV no tiene columna id_cuenta (ni alias). Headers: {headers}")
        return
    seg_cols = [h for h, c in colmap.items() if c in MANUAL_FIELDS]
    if not seg_cols:
        print(f"❌ El CSV no tiene ninguna columna de segmentación. Headers: {headers}")
        return
    print(f"Separador: {dialect.delimiter!r} · columnas mapeadas: {colmap}")
    print(f"Filas en CSV: {len(rows)}\n")

    col = get_mongo_client()[DB][COL]
    now = datetime.now(UTC)
    actualizadas = no_encontradas = sin_datos = 0
    faltantes: list[str] = []

    for row in rows:
        id_cuenta = ""
        set_fields: dict[str, str] = {}
        for h, canon in colmap.items():
            val = (row.get(h) or "").strip()
            if canon == "id_cuenta":
                id_cuenta = val
            elif val:  # celda vacía → no pisar
                set_fields[canon] = val
        if not id_cuenta:
            continue
        if not set_fields:
            sin_datos += 1
            continue

        if args.dry_run:
            if col.count_documents({"id_cuenta": id_cuenta}, limit=1):
                actualizadas += 1
                print(f"  [dry] {id_cuenta} ← {set_fields}")
            else:
                no_encontradas += 1
                faltantes.append(id_cuenta)
            continue

        set_fields["seg_updated_at"] = now
        res = col.update_one({"id_cuenta": id_cuenta}, {"$set": set_fields})
        if res.matched_count:
            actualizadas += 1
        else:
            no_encontradas += 1
            faltantes.append(id_cuenta)

    print(
        f"\nResumen: {actualizadas} actualizadas · {no_encontradas} no encontradas "
        f"en el master · {sin_datos} filas sin datos de segmentación"
    )
    if faltantes:
        print(f"id_cuenta no encontradas (¿corriste sync_comitentes?): {faltantes[:30]}")
    if args.dry_run:
        print("(DRY-RUN — no se escribió nada)")


if __name__ == "__main__":
    main()
