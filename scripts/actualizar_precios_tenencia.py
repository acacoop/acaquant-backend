"""scripts/actualizar_precios_tenencia.py — actualiza `precio` en portafolio.tenencia desde archivo.

Subís un archivo (CSV o Excel) con los precios CORRECTOS de una fecha (arrancamos por fines
de mes). El script matchea por `unidad` (la clave común) y actualiza la columna `precio` de
`portafolio.tenencia` para esa fecha. NO recalcula `valuacion` todavía (eso es el paso 2 —
ahí se aplica /100 a bonos para no inflar). Solo toca `precio`.

Idempotente. `--dry-run` muestra qué matchea y qué NO (para cazar mismatches de formato)
sin escribir nada.

Precio: base 100 para bonos / normal para el resto (tal cual viene en el archivo — acá NO
se divide; el /100 va en el recálculo de valuación).

Uso:
    python -m scripts.actualizar_precios_tenencia precios_2026-05-29.xlsx 2026-05-29 --dry-run
    python -m scripts.actualizar_precios_tenencia precios_2026-05-29.xlsx 2026-05-29
    # columnas distintas:
    python -m scripts.actualizar_precios_tenencia archivo.csv 2026-05-29 --col-unidad ESPECIE --col-precio PX
"""
from __future__ import annotations

import sys

import pandas as pd

from core.postgres import get_pool


def _opt(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _leer(path: str) -> pd.DataFrame:
    low = path.lower()
    if low.endswith(".csv"):
        return pd.read_csv(path)
    if low.endswith((".xlsx", ".xls")):
        return pd.read_excel(path)
    raise SystemExit(f"Formato no soportado: {path} (usá .csv o .xlsx)")


def main() -> int:
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(pos) < 2:
        print("Uso: python -m scripts.actualizar_precios_tenencia <archivo> <YYYY-MM-DD> "
              "[--col-unidad unidad] [--col-precio precio] [--dry-run]")
        return 1
    path, fecha = pos[0], pos[1]
    col_u = _opt("--col-unidad", "unidad")
    col_p = _opt("--col-precio", "precio")
    dry = "--dry-run" in sys.argv

    df = _leer(path)
    # Match de columnas case-insensitive (el archivo trae 'Unidad'/'Precio' con mayúscula).
    cols_lower = {str(c).strip().lower(): c for c in df.columns}
    cu = cols_lower.get(col_u.lower())
    cp = cols_lower.get(col_p.lower())
    if not cu or not cp:
        print(f"✗ No encuentro las columnas '{col_u}' / '{col_p}'.")
        print(f"  Columnas del archivo: {list(df.columns)}")
        print("  Pasá los nombres reales con --col-unidad y --col-precio.")
        return 1

    # Normalizar: unidad str, precio numérico (descarta filas sin precio válido).
    precios: dict[str, float] = {}
    descartadas = 0
    for _, row in df.iterrows():
        u = str(row[cu]).strip()
        px = pd.to_numeric(row[cp], errors="coerce")
        if not u or pd.isna(px):
            descartadas += 1
            continue
        precios[u] = float(px)

    print(f"archivo: {len(df)} filas · precios válidos (unidad→precio): {len(precios)} "
          f"· descartadas (sin unidad/precio): {descartadas}")

    # Qué unidades existen en tenencia para esa fecha.
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT unidad, count(*) FROM portafolio.tenencia "
                    "WHERE fecha = %s GROUP BY unidad", (fecha,))
        en_tenencia = {u: n for u, n in cur.fetchall()}

        matched = [u for u in precios if u in en_tenencia]
        sin_match = [u for u in precios if u not in en_tenencia]
        filas_afectadas = sum(en_tenencia[u] for u in matched)

        print(f"fecha {fecha}: unidades distintas en tenencia = {len(en_tenencia)}")
        print(f"  matchean (se actualizan): {len(matched)} unidades → {filas_afectadas} filas")
        print(f"  del archivo SIN match en esa fecha: {len(sin_match)}")
        for u in sin_match[:15]:
            print(f"      · {u}")
        if len(sin_match) > 15:
            print(f"      … (+{len(sin_match) - 15} más)")

        if dry:
            print("(--dry-run: no se escribió nada)")
            return 0

        # UPDATE por unidad (solo precio; valuacion se recalcula en el paso 2).
        actualizadas = 0
        for u in matched:
            cur.execute("UPDATE portafolio.tenencia SET precio = %(p)s "
                        "WHERE fecha = %(f)s AND unidad = %(u)s",
                        {"p": precios[u], "f": fecha, "u": u})
            actualizadas += cur.rowcount
        conn.commit()

    print(f"✅ precio actualizado en {actualizadas} filas de portafolio.tenencia ({fecha}).")
    print("   ⚠️ La `valuacion` quedó SIN recalcular (paso 2). Hasta entonces precio≠valuación.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
