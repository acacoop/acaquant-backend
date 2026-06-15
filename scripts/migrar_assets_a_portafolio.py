"""scripts/migrar_assets_a_portafolio.py — mueve la tabla `assets` del schema
`public` al schema `portafolio` y le agrega las columnas que faltaban para que
SQL sea la fuente de verdad de los metadatos de títulos (segmentación).

Qué hace (idempotente, se puede re-correr):
  1. CREATE SCHEMA portafolio (si no existe).
  2. Si `public.assets` existe y `portafolio.assets` no → ALTER ... SET SCHEMA
     portafolio (mueve la tabla CON sus datos, PK e índices — no copia, renombra).
  3. CREATE TABLE IF NOT EXISTS portafolio.assets (fallback para DB fresca).
  4. ADD COLUMN IF NOT EXISTS: vencimiento, codigo_cnv, fee_admin,
     actualizado_por, actualizado_at (los que edita el panel Manager → Assets).
  5. Recrea los índices (qualified) por las dudas.

    python -m scripts.migrar_assets_a_portafolio            # PREVIEW (no escribe)
    python -m scripts.migrar_assets_a_portafolio --commit   # aplica

Tras correrlo: `git pull` + `systemctl restart api.service` para que la API tome
el código que ya apunta a `portafolio.assets`. Orden: migración PRIMERO, restart
DESPUÉS (el código nuevo referencia portafolio.assets).
"""
from __future__ import annotations

import sys

from core.postgres import get_pool

_DDL = [
    "CREATE SCHEMA IF NOT EXISTS portafolio",
    # Mover public.assets → portafolio.assets SOLO si hace falta (idempotente).
    """DO $$
       BEGIN
         IF to_regclass('public.assets') IS NOT NULL
            AND to_regclass('portafolio.assets') IS NULL THEN
           ALTER TABLE public.assets SET SCHEMA portafolio;
         END IF;
       END $$""",
    # Fallback para DB fresca (si nunca existió public.assets).
    """CREATE TABLE IF NOT EXISTS portafolio.assets (
         unidad       text PRIMARY KEY,
         cartera      text, clase_activo text, emisor text, ticker text,
         instrumento  text, calificacion text, cafci text)""",
    # Columnas que edita el panel Manager → Assets (faltaban en SQL).
    "ALTER TABLE portafolio.assets ADD COLUMN IF NOT EXISTS vencimiento text",
    "ALTER TABLE portafolio.assets ADD COLUMN IF NOT EXISTS codigo_cnv text",
    "ALTER TABLE portafolio.assets ADD COLUMN IF NOT EXISTS fee_admin numeric",
    "ALTER TABLE portafolio.assets ADD COLUMN IF NOT EXISTS actualizado_por text",
    "ALTER TABLE portafolio.assets ADD COLUMN IF NOT EXISTS actualizado_at timestamptz",
    "CREATE INDEX IF NOT EXISTS ix_assets_cartera ON portafolio.assets(cartera)",
    "CREATE INDEX IF NOT EXISTS ix_assets_clase   ON portafolio.assets(clase_activo)",
    "CREATE INDEX IF NOT EXISTS ix_assets_ticker  ON portafolio.assets(ticker)",
]


def _estado(cur) -> None:
    cur.execute("SELECT to_regclass('public.assets'), to_regclass('portafolio.assets')")
    pub, por = cur.fetchone()
    print(f"   public.assets     : {pub or '—'}")
    print(f"   portafolio.assets : {por or '—'}")
    for schema, tabla in (("public", "assets"), ("portafolio", "assets")):
        if (schema == "public" and pub) or (schema == "portafolio" and por):
            cur.execute(f"SELECT count(*) FROM {schema}.{tabla}")
            print(f"   filas en {schema}.{tabla}: {cur.fetchone()[0]}")


def main() -> None:
    commit = "--commit" in sys.argv
    with get_pool().connection() as conn, conn.cursor() as cur:
        print("\n=== ANTES ===")
        _estado(cur)
        if not commit:
            print("\n[PREVIEW] no se escribió nada. Repetí con --commit para aplicar.\n")
            return
        for stmt in _DDL:
            cur.execute(stmt)
        conn.commit()
        print("\n=== DESPUÉS ===")
        _estado(cur)
        print("\n[COMMIT] listo. Hacé git pull + systemctl restart api.service.\n")


if __name__ == "__main__":
    main()
