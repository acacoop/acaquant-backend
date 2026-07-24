"""diag_divisor_bono.py — READ-ONLY: cómo trata el sistema a un título en las
DOS capas del divisor de valuación (÷100 vs ×1).

Capa 1 (AuM, writer diario): el divisor lo decide el `tipoTitulo` de AUNESA
(lista TIPOS_DIVISOR_100 en jobs/aum.py) — NO se persiste en la base, así que
acá se INFIERE del cociente valuacion/(precio×cantidad) de la última tenencia:
≈0.01 → se dividió por 100 · ≈1 → no se dividió.

Capa 2 (PnL/valor live): la decide la CARTERA del asset (portafolio.assets):
HD/DL/ARS → ÷100 · FCI/RENTA VARIABLE/MONEDAS/DERIVADOS → ×1 · vacía → cae al
fallback legacy por tipoTitulo.

Uso:  python -m scripts.diag_divisor_bono S13N6
      (acepta parte de la `unidad`, matchea con ILIKE)
"""
from __future__ import annotations

import sys

from psycopg.rows import dict_row

from core.postgres import get_pool

_DIV100 = {"HD", "DL", "ARS"}
_DIV1 = {"FCI", "RENTA VARIABLE", "MONEDAS", "DERIVADOS"}


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def main() -> None:
    patron = sys.argv[1] if len(sys.argv) > 1 else "S13N6"
    like = f"%{patron}%"
    print(f"== Diagnóstico del divisor para unidad ~ {patron!r} ==\n")

    assets = _q(
        "SELECT unidad, cartera, ticker, clase_activo, emisor, instrumento "
        "FROM portafolio.assets WHERE unidad ILIKE %s ORDER BY unidad", (like,))
    print(f"── portafolio.assets ({len(assets)} match) ──")
    if not assets:
        print("  SIN FILA en assets → el writer no le conoce cartera (va NULL a")
        print("  tenencia) y el PnL cae al fallback por tipoTitulo. Además sin")
        print("  fila+ticker no aparece linkeado en AuM/Portfolios.")
    for a in assets:
        c = (a["cartera"] or "").strip().upper()
        if c in _DIV100:
            regla = f"cartera {c!r} → PnL/live DIVIDE por 100 (renta fija en paridad)"
        elif c in _DIV1:
            regla = f"cartera {c!r} → PnL/live NO divide (×1)"
        elif c:
            regla = f"cartera {c!r} DESCONOCIDA para el normalizer → fallback por tipoTitulo"
        else:
            regla = "cartera VACÍA → fallback legacy por tipoTitulo (clasificar en Manager→Assets)"
        print(f"  {a['unidad']}")
        print(f"      cartera={a['cartera']!r}  ticker={a['ticker']!r}  "
              f"clase_activo={a['clase_activo']!r}  emisor={a['emisor']!r}")
        print(f"      → {regla}")

    print("\n── portafolio.tenencia (última fecha con esa unidad) ──")
    ten = _q(
        "SELECT fecha, id_cuenta, unidad, cartera, cantidad, precio, valuacion, aum "
        "FROM portafolio.tenencia WHERE unidad ILIKE %s "
        "AND fecha = (SELECT max(fecha) FROM portafolio.tenencia WHERE unidad ILIKE %s) "
        "ORDER BY id_cuenta", (like, like))
    if not ten:
        print("  SIN FILAS en tenencia → esa unidad no vino en la posición de")
        print("  Aunesa (o todavía no corrió el writer diario de las 11:00 UTC).")
    for t in ten:
        prec = float(t["precio"] or 0)
        cant = float(t["cantidad"] or 0)
        val = float(t["valuacion"] or 0)
        if prec and cant:
            ratio = val / (prec * cant)
            if abs(ratio - 0.01) < 0.001:
                aplicado = "÷100 (renta fija en paridad)"
            elif abs(ratio - 1.0) < 0.05:
                aplicado = "×1 (SIN dividir)"
            else:
                aplicado = f"ratio raro: {ratio:.6f} (¿futuro precio+1 / dato sucio?)"
        else:
            aplicado = "no inferible (precio o cantidad en 0)"
        print(f"  {t['fecha']}  cta {t['id_cuenta']:<6} cant={cant:>18,.2f}  "
              f"precio={prec:>12,.4f}  valuacion={val:>18,.2f}  aum={t['aum']}")
        print(f"      cartera guardada={t['cartera']!r}  ·  divisor APLICADO por el writer: {aplicado}")

    print("\nLectura: si el writer aplicó ×1 y es un bono/letra en paridad, el")
    print("tipoTitulo de Aunesa no está en TIPOS_DIVISOR_100 (jobs/aum.py) —")
    print("la valuación del AuM queda ×100. Si además la cartera está vacía,")
    print("clasificarla en Manager→Assets (HD/DL/ARS) arregla el PnL/live; el")
    print("AuM se corrige recién en la próxima corrida del writer (11:00 UTC).")


if __name__ == "__main__":
    main()
