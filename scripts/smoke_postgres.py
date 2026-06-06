"""scripts/smoke_postgres.py — smoke test de la conexión a Postgres (Supabase).

Verifica que (1) POSTGRES_URI está en el .env, (2) conecta, (3) ve las tablas que
creó sql/schema.sql. 100% lectura. Correr tras crear el proyecto Supabase, aplicar
sql/schema.sql y poner POSTGRES_URI en el .env.

    python -m scripts.smoke_postgres
"""
from __future__ import annotations

from core.postgres import connect

_ESPERADAS = {
    "operadores", "cuentas", "comitentes", "contrapartes",
    "operaciones", "aum", "negocio_movimientos",
}


def main() -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
        tablas = [r[0] for r in cur.fetchall()]
        print(f"✅ Conectado a Postgres. {len(tablas)} tablas en 'public':")
        for t in tablas:
            cur.execute(f'SELECT count(*) FROM "{t}"')  # t viene de information_schema (confiable)
            print(f"   {t:22} {cur.fetchone()[0]:>10,} filas")

        faltan = _ESPERADAS - set(tablas)
        if faltan:
            print(f"\n⚠ Faltan tablas del schema: {sorted(faltan)} — ¿corriste sql/schema.sql?")
            return 1
        print("\nSchema OK. Todo vacío (esperado). Listo para la Fase B (sync Mongo→Postgres).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
