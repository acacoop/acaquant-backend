"""scripts/fix_assets_trim.py — limpia espacios al borde en los campos string de portafolio.assets.

Un valor como 'HD  ' (con espacios) rompe los filtros que comparan exacto: la cartera HD/MONEDAS
de Tenencia Valorizada, el divisor del AuM por cartera, los dropdowns, etc. Esto recorta (TRIM)
los campos string de TODO el catálogo. Solo toca filas sucias (value <> TRIM(value)); NULLs y
valores ya limpios no se tocan. Idempotente.

    python -m scripts.fix_assets_trim            # dry-run: muestra qué corregiría
    python -m scripts.fix_assets_trim --apply    # corrige de verdad

Scopeado (solo filas sucias) + idempotente. Borrar tras usar (REGLA #5).
"""
from __future__ import annotations

import sys

COLS = ["cartera", "emisor", "instrumento", "clase_activo",
        "calificacion", "ticker", "vencimiento", "codigo_cnv"]


def main() -> int:
    apply = "--apply" in sys.argv
    from core.postgres import connect

    cond = " OR ".join(f"{c} IS DISTINCT FROM TRIM({c})" for c in COLS)
    with connect() as conn, conn.cursor() as cur:
        # Mostrar qué está sucio (por celda), para auditar antes de tocar.
        print(f"Campos con espacios al borde en portafolio.assets  (modo {'APPLY' if apply else 'DRY-RUN'}):\n")
        total = 0
        for c in COLS:
            cur.execute(
                f"SELECT unidad, {c} FROM portafolio.assets "
                f"WHERE {c} IS DISTINCT FROM TRIM({c}) ORDER BY unidad")
            filas = cur.fetchall()
            for u, val in filas:
                print(f"  {c:<13} [{val}] → [{(val or '').strip()}]   unidad={u}")
                total += 1
        if total == 0:
            print("  (nada sucio — todo limpio)")
            return 0

        if apply:
            sets = ", ".join(f"{c} = TRIM({c})" for c in COLS)
            cur.execute(f"UPDATE portafolio.assets SET {sets} WHERE {cond}")
            n = cur.rowcount
            conn.commit()
            print(f"\n✅ {n} fila(s) corregida(s).")
        else:
            print(f"\nDRY-RUN: {total} celda(s) sucia(s). Re-correr con --apply para corregir.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
