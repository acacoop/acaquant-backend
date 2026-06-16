"""scripts/migrar_schemas_clientes_operaciones.py — organiza las tablas SQL en
schemas por DOMINIO (en vez de todo desparramado en `public` como venía de Mongo).

Schemas destino:
  - clientes    → cuentas, operadores, comitentes, contrapartes, accionistas,
                  actividad_mensual  (cuentas + segmentación)
  - operaciones → operaciones, negocio_movimientos  (operaciones + movimientos)
  - portafolio  → tenencia, assets  (ya existía)

Mueve cada tabla SOLO si todavía está en `public` (idempotente). El código sigue
funcionando sin tocar queries gracias al `search_path` de core/postgres.py
(clientes, operaciones, portafolio, public).

También agrega las columnas que faltaban para poder ESCRIBIR todo a SQL (segmentación
de comitentes + auditoría de contrapartes).

    python -m scripts.migrar_schemas_clientes_operaciones            # PREVIEW
    python -m scripts.migrar_schemas_clientes_operaciones --commit   # aplica

Idempotente: re-correrlo no rompe nada.
"""
from __future__ import annotations

import sys

from core.postgres import get_pool

# (tabla, schema destino)
_MOVE = [
    ("cuentas", "clientes"), ("operadores", "clientes"), ("comitentes", "clientes"),
    ("contrapartes", "clientes"), ("accionistas", "clientes"),
    ("actividad_mensual", "clientes"),
    ("operaciones", "operaciones"), ("negocio_movimientos", "operaciones"),
]

# Columnas que faltaban para escribir todo a SQL (eran subdocs/campos en Mongo).
_ADD_COLS = [
    # clientes.comitentes (segmentación + auditoría)
    ("comitentes", "observaciones", "text"),
    ("comitentes", "sucursal", "text"),
    ("comitentes", "segmento_patrimonial", "text"),
    ("comitentes", "origen", "text"),
    ("comitentes", "cupo_utilizacion_pct", "numeric"),
    ("comitentes", "cupo_cargado_en", "timestamptz"),
    ("comitentes", "cupo_fuente", "text"),
    ("comitentes", "actualizado_por", "text"),
    ("comitentes", "actualizado_at", "timestamptz"),
    # clientes.contrapartes (auditoría del panel)
    ("contrapartes", "origen", "text"),
    ("contrapartes", "actualizado_por", "text"),
    ("contrapartes", "actualizado_at", "timestamptz"),
]


def _schema_de(cur, tabla: str) -> str | None:
    cur.execute("SELECT schemaname FROM pg_tables WHERE tablename = %s "
                "AND schemaname IN ('public','clientes','operaciones','portafolio')", (tabla,))
    r = cur.fetchone()
    return r[0] if r else None


def main() -> None:
    commit = "--commit" in sys.argv
    with get_pool().connection() as conn, conn.cursor() as cur:
        print("\n=== ANTES (schema actual de cada tabla) ===")
        for tabla, destino in _MOVE:
            print(f"   {tabla:<22} → {_schema_de(cur, tabla) or '(no existe)':<12} (destino: {destino})")

        if not commit:
            print("\n[PREVIEW] no se escribió nada. Repetí con --commit para aplicar.\n")
            return

        cur.execute("CREATE SCHEMA IF NOT EXISTS clientes")
        cur.execute("CREATE SCHEMA IF NOT EXISTS operaciones")
        cur.execute("CREATE SCHEMA IF NOT EXISTS portafolio")
        for tabla, destino in _MOVE:
            actual = _schema_de(cur, tabla)
            if actual and actual != destino:
                cur.execute(f"ALTER TABLE {actual}.{tabla} SET SCHEMA {destino}")
                print(f"   ✓ movida {actual}.{tabla} → {destino}.{tabla}")
            elif actual == destino:
                print(f"   — {tabla} ya estaba en {destino}")
            else:
                print(f"   ! {tabla} no existe (se creará al migrar su writer)")
        for tabla, col, tipo in _ADD_COLS:
            sch = _schema_de(cur, tabla) or "clientes"
            cur.execute(f"ALTER TABLE {sch}.{tabla} ADD COLUMN IF NOT EXISTS {col} {tipo}")
        conn.commit()

        print("\n=== DESPUÉS ===")
        for tabla, _destino in _MOVE:
            print(f"   {tabla:<22} → {_schema_de(cur, tabla) or '(no existe)'}")
        print("\n[COMMIT] listo. git pull + systemctl restart api.service (toma el search_path).\n")


if __name__ == "__main__":
    main()
