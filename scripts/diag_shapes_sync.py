"""scripts/diag_shapes_sync.py — radiografía de las colecciones fuente del sync a Postgres.

100% LECTURA. Muestrea cada colección Mongo que alimenta una tabla de sql/schema.sql
y reporta: conteo aprox, los campos top-level (unión sobre una muestra), el tipo Python
de cada uno y un valor de ejemplo (truncado). Sirve para mapear Mongo→Postgres SIN
asumir nombres/shapes (REGLA #2) antes de escribir jobs/sync_postgres.py.

    python -m scripts.diag_shapes_sync
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read

# (db, coleccion, tabla_destino_en_postgres). Si alguna fuente no es la correcta,
# lo vemos acá y se corrige antes de codear el sync.
FUENTES = [
    ("Manager", "Users", "operadores"),
    ("Clientes", "Comitentes", "comitentes / cuentas"),
    ("CashFlow", "Contrapartes", "contrapartes"),
    ("CashFlow", "Operaciones", "operaciones"),
    ("Valuaciones", "AuM", "aum"),
    ("CashFlow", "NegocioMovimientos", "negocio_movimientos"),
]

MUESTRA = 200  # docs a muestrear por colección para juntar el universo de campos


def _tipo(v) -> str:
    return type(v).__name__


def _ej(v) -> str:
    s = repr(v)
    return s if len(s) <= 60 else s[:57] + "..."


def main() -> int:
    cli = get_mongo_client_read()
    for db_name, col_name, destino in FUENTES:
        col = cli[db_name][col_name]
        print("=" * 78)
        print(f"{db_name}.{col_name}   →  tabla(s): {destino}")
        try:
            total = col.estimated_document_count()
        except Exception as e:
            total = f"(error: {e})"
        print(f"  conteo aprox: {total:,}" if isinstance(total, int) else f"  conteo: {total}")

        # Universo de campos top-level sobre una muestra + último valor visto de cada uno.
        campos: dict[str, tuple[str, object]] = {}
        n = 0
        for doc in col.find({}, limit=MUESTRA):
            n += 1
            for k, v in doc.items():
                campos[k] = (_tipo(v), v)
        if n == 0:
            print("  (vacía)")
            continue
        print(f"  campos (sobre {n} docs muestreados):")
        for k in sorted(campos):
            t, v = campos[k]
            print(f"    {k:24} {t:10} ej={_ej(v)}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
