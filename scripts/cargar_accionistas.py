"""scripts/cargar_accionistas.py — carga manual de accionistas en SQL `clientes.accionistas`.

SQL-NATIVE (decomiso Mongo): reemplaza la carga manual en `CashFlow.Accionistas` (Mongo).
La tabla la leen `api/routers/cuentas.listar_accionistas` (dropdown, grupo=accionista),
`api/services/_cuentas_filter` (filtro de cuenta accionistas/sin_accionistas/cooperativas)
y `api/services/risk` (nombres por id_cuenta).

Tabla: `clientes.accionistas` (cuenta PK, accionista). `cuenta` = '[N] NOMBRE'. La columna
`accionista` (grupo) sólo se puebla desde acá — el puente `sync_accionistas` (backstop)
sincroniza únicamente `cuenta` y NO pisa `accionista`.

Uso:
    # Alta/edición de una cuenta (cuenta + grupo accionista)
    python -m scripts.cargar_accionistas --cuenta "[534] EGUREN, NE" --accionista "GRUPO X"
"""
from __future__ import annotations

import argparse

from core.postgres import get_pool

_UPSERT = (
    "INSERT INTO accionistas (cuenta, accionista) VALUES (%s, %s) "
    "ON CONFLICT (cuenta) DO UPDATE SET accionista = EXCLUDED.accionista"
)


def _upsert_rows(rows: list[tuple[str, str | None]]) -> int:
    if not rows:
        return 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(_UPSERT, rows)
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Carga manual de accionistas en SQL.")
    ap.add_argument("--cuenta", help="string '[N] NOMBRE' (PK).")
    ap.add_argument("--accionista", default=None, help="nombre del grupo accionista.")
    args = ap.parse_args()

    if not args.cuenta:
        ap.error("--cuenta es obligatorio.")
    cuenta = args.cuenta.strip()
    acc = args.accionista.strip() if args.accionista else None
    _upsert_rows([(cuenta, acc)])
    print(f"Accionista cargado: cuenta={cuenta!r} accionista={acc!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
