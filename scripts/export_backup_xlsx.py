"""export_backup_xlsx.py — exporta las collections _old a XLSX local.

Genera 2 archivos en ./exports/:
  - timesales_old_YYYYMMDD.xlsx       (Trading.TimeSales_old, 898k filas aprox)
  - opciones_data_old_YYYYMMDD.xlsx   (Opciones.Data_old, 195k filas aprox)

Usa openpyxl en write_only para streamear sin volar memoria. Auto-detecta
columnas leyendo los primeros 2000 docs (cubre el caso de campos
heterogéneos por motivos legacy).

Pre-condiciones:
    pip install openpyxl    (si no está en el venv)

Uso:
    python -m scripts.export_backup_xlsx
    python -m scripts.export_backup_xlsx --skip-timesales   # solo Opciones
    python -m scripts.export_backup_xlsx --skip-opciones    # solo TimeSales

Después:
    ls -la ~/TradingAV/exports/
    # Para descargar: scp en tu máquina local.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from core.mongo import get_mongo_client

EXPORT_DIR = Path(__file__).resolve().parent.parent / "exports"
SAMPLE_SIZE_FOR_COLUMNS = 2000

# Orden preferido para que las columnas más importantes queden primero.
PRIORITY_COLS = ["timestamp", "ticker", "symbol", "price", "size", "side",
                 "money", "tipo", "strike", "spot", "bid", "offer", "last"]


def _detect_columns(col, sample_size: int = SAMPLE_SIZE_FOR_COLUMNS) -> list[str]:
    """Lee N docs y devuelve la unión de keys, ordenadas con prioridad."""
    keys: set[str] = set()
    for doc in col.find({}, limit=sample_size):
        keys.update(doc.keys())
    keys.discard("_id")
    ordered = [k for k in PRIORITY_COLS if k in keys]
    rest = sorted(k for k in keys if k not in PRIORITY_COLS)
    return ordered + rest


def _serialize(v):
    """Convierte tipos Mongo a primitivos compatibles con XLSX."""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def export_collection(db_name: str, col_name: str, output_path: Path) -> None:
    try:
        from openpyxl import Workbook
    except ImportError:
        print("\n[!! ] openpyxl no está instalado. Instalalo con:")
        print("    pip install openpyxl\n")
        raise

    client = get_mongo_client()
    col = client[db_name][col_name]
    total = col.count_documents({})

    print(f"\n  ┌─ {db_name}.{col_name}")
    print(f"  │  docs totales : {total:,}")

    if total == 0:
        print("  │  [SKIP] colección vacía.")
        print("  └────────────────────────")
        return

    if total > 1_000_000:
        print(f"  │  [WARN] {total:,} > 1M filas (límite Excel). "
              f"Voy a truncar al primer millón.")

    print(f"  │  detectando columnas (sample {SAMPLE_SIZE_FOR_COLUMNS:,})…")
    columns = _detect_columns(col)
    print(f"  │  columnas: {len(columns)} → {columns[:6]}{'…' if len(columns) > 6 else ''}")

    print(f"  │  destino : {output_path}")
    print("  │  exportando…")

    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title="data")
    ws.append(columns)

    sort_field = "timestamp" if any(
        col_name == c for c in ("TimeSales_old", "Data_old")
    ) else None

    cursor = col.find({}, sort=[(sort_field, 1)] if sort_field else None)
    written = 0
    log_every = 50_000
    for doc in cursor:
        if written >= 1_048_575:  # límite Excel - 1 (header)
            break
        row = [_serialize(doc.get(c)) for c in columns]
        ws.append(row)
        written += 1
        if written % log_every == 0:
            print(f"  │  ... {written:,}/{total:,}")

    print("  │  guardando archivo…")
    wb.save(output_path)
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"  │  [OK] {written:,} filas escritas · {size_mb:.1f} MB")
    print("  └────────────────────────")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-timesales", action="store_true")
    parser.add_argument("--skip-opciones", action="store_true")
    args = parser.parse_args()

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 60)
    print(f" Export backup → {EXPORT_DIR}")
    print("=" * 60)

    if not args.skip_timesales:
        export_collection(
            "Trading", "TimeSales_old",
            EXPORT_DIR / f"timesales_old_{fecha}.xlsx",
        )

    if not args.skip_opciones:
        export_collection(
            "Opciones", "Data_old",
            EXPORT_DIR / f"opciones_data_old_{fecha}.xlsx",
        )

    print("\n" + "=" * 60)
    print(" ✓ Export completo. Para descargar a tu máquina local:")
    print(f"    scp root@<droplet>:{EXPORT_DIR}/*.xlsx ~/Downloads/")
    print(" Después podés borrar las collections _old con cleanup --yes.")
    print("=" * 60)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
