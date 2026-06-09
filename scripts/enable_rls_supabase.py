"""scripts/enable_rls_supabase.py — habilita RLS en las tablas public de Supabase.

Arregla el alert de seguridad `rls_disabled_in_public`: sin RLS, cualquiera con
la URL del proyecto + la anon key puede leer/editar las tablas vía la REST API
de Supabase (PostgREST). Nuestro espejo es de SOLO LECTURA y la app se conecta
por psycopg como rol `postgres` (superusuario) → BYPASSEA RLS. La REST API usa
`anon`/`authenticated` → al habilitar RLS SIN políticas, esos quedan denegados
(que es justo lo que queremos) y la app sigue leyendo igual.

  (default, dry-run)  python -m scripts.enable_rls_supabase
      No cambia nada. Muestra: con qué ROL conectás (y si saltea RLS) + qué
      tablas de `public` tienen RLS y cuáles no.

  (aplica)  python -m scripts.enable_rls_supabase --commit
      ALTER TABLE ... ENABLE ROW LEVEL SECURITY en todas las public sin RLS.
      ABORTA si el rol de conexión NO saltea RLS (te dejaría sin lecturas).

REGLA #0: en vez de pegar SQL en el dashboard de Supabase, corrés esto en el
Droplet con la misma conexión que usa la app.
"""
from __future__ import annotations

import argparse

from core.postgres import connect


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="aplica el ENABLE RLS")
    args = ap.parse_args()

    with connect() as conn, conn.cursor() as cur:
        # 1) Rol de conexión + ¿saltea RLS?
        cur.execute("SELECT current_user, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user")
        usuario, es_super, bypass = cur.fetchone()
        saltea = bool(es_super or bypass)
        print(f"Conectado como: {usuario}  ·  superuser={es_super}  ·  bypassrls={bypass}")
        print(f"→ {'SALTEA RLS (seguro habilitar)' if saltea else '⚠ NO saltea RLS — habilitar te cortaría las lecturas'}\n")

        # 2) Tablas de public + estado de RLS.
        cur.execute("""
            SELECT c.relname, c.relrowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY c.relname
        """)
        tablas = cur.fetchall()
        sin_rls = [t for t, rls in tablas if not rls]
        con_rls = [t for t, rls in tablas if rls]
        print(f"Tablas en public: {len(tablas)}  ·  con RLS: {len(con_rls)}  ·  SIN RLS: {len(sin_rls)}")
        if sin_rls:
            print("  SIN RLS (expuestas): " + ", ".join(sin_rls))

        if not sin_rls:
            print("\n✅ Todas las tablas public ya tienen RLS. Nada que hacer.")
            return

        if not args.commit:
            print("\n(DRY-RUN — no se cambió nada. Corré con --commit para habilitar RLS "
                  "en las expuestas.)")
            return

        if not saltea:
            print("\n⛔ ABORTO: el rol de conexión NO saltea RLS. Habilitar RLS te dejaría "
                  "sin lecturas. Conectá como `postgres` (superuser) y reintentá.")
            return

        # 3) Habilitar RLS en las expuestas (sin políticas → anon/authenticated denegados).
        for t in sin_rls:
            cur.execute(f'ALTER TABLE public."{t}" ENABLE ROW LEVEL SECURITY;')
        conn.commit()
        print(f"\n✅ RLS habilitado en {len(sin_rls)} tablas. La REST API anon ya no las lee; "
              "la app (rol postgres) sigue igual. Re-corré el security advisor de Supabase.")


if __name__ == "__main__":
    main()
