"""diag_cuenta.py — por qué una cuenta NO aparece en el buscador de Operaciones.

El buscador (/api/operaciones/ops/cuentas-list) lista SOLO cuentas con boletos en
`operaciones.operaciones`. Este diag mide, para una cuenta dada:
  1) si está en el maestro `clientes.comitentes`,
  2) cuántos boletos tiene en `operaciones.operaciones`,
  3) si APARECE en el buscador (corre la query real),
  4) si hay variantes de formato del id (espacios, ceros, etc.) que la escondan.

Read-only. Uso:
    python -m scripts.diag_cuenta 1839
"""
from __future__ import annotations

import sys

from psycopg.rows import dict_row

from core.postgres import get_pool


def _run(cur, label, sql, params):
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    except Exception as e:
        print(f"    [!] {label}: {str(e).splitlines()[0][:160]}")
        return []


def main(idc: str) -> None:
    idc = idc.strip()
    like = f"%{idc}%"
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        print(f"=== Cuenta {idc!r} ===\n")

        # 1) Maestro de cuentas
        m = _run(cur, "comitentes exacto",
                 "SELECT * FROM clientes.comitentes WHERE id_cuenta = %s", (idc,))
        print(f"[1] clientes.comitentes (exacto): {len(m)} fila(s)")
        for r in m:
            print("    ", {k: r[k] for k in list(r)[:9]})
        mv = _run(cur, "comitentes variantes",
                  "SELECT id_cuenta FROM clientes.comitentes "
                  "WHERE id_cuenta LIKE %s OR trim(id_cuenta) = %s", (like, idc))
        print(f"    variantes (LIKE/trim): {[r['id_cuenta'] for r in mv]}")

        # 2) Boletos
        b = _run(cur, "boletos exacto",
                 "SELECT count(*) n, max(denominacion) d FROM operaciones.operaciones "
                 "WHERE id_cuenta = %s", (idc,))
        if b:
            print(f"\n[2] operaciones.operaciones (exacto): {b[0]['n']} boletos · denom={b[0]['d']!r}")
        bv = _run(cur, "boletos variantes",
                  "SELECT id_cuenta, count(*) n, max(denominacion) d "
                  "FROM operaciones.operaciones "
                  "WHERE id_cuenta LIKE %s OR trim(id_cuenta) = %s GROUP BY id_cuenta",
                  (like, idc))
        print(f"    variantes (LIKE/trim): {[(r['id_cuenta'], r['n'], r['d']) for r in bv]}")

        # 3) ¿Aparece en el buscador? (query real del endpoint)
        en = _run(cur, "buscador",
                  "SELECT 1 FROM (SELECT id_cuenta FROM operaciones.operaciones "
                  "WHERE id_cuenta IS NOT NULL GROUP BY id_cuenta) t WHERE id_cuenta = %s", (idc,))
        print(f"\n[3] ¿Aparece en el buscador (query real)?: {bool(en)}")

    print("\n=== CÓMO LEERLO ===")
    print("- En comitentes pero 0 boletos  → caso A: el buscador (lee boletos) no la muestra.")
    print("                                   Fix: que el buscador sume el maestro.")
    print("- NI comitentes NI boletos      → caso B: no se sincronizó/ingestó (upstream).")
    print("- Variante con espacios/ceros   → bug de formato del id_cuenta en la ingesta.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "1839")
