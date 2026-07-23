"""diag_fci_vacios.py — READ-ONLY: por qué hay instrumentos FCI con NOMBRE VACÍO
en el detalle de /aum → FCI, y si esos mismos aparecen en /aum → TOTAL.

Contexto del reporte del user: en la tabla de detalle de FCI (endpoint
`/api/portfolio/fci-snapshot` → `portfolio_sql.fci_snapshot`) hay filas cuyo
nombre (emisor/ticker) figura vacío; y esa misma posición aparece luego en la
vista AuM TOTAL (`total_snapshot`).

El detalle FCI se arma con:
    portafolio.tenencia v  JOIN  portafolio.assets a  ON a.unidad = v.unidad
    WHERE a.cartera IN ('FCI','CARTERA FCI')  AND  v.aum='si'
y proyecta  emisor = COALESCE(NULLIF(a.emisor,''),'SIN EMISOR')  ·
            ticker = COALESCE(a.ticker,'')

→ Un nombre vacío puede venir de DOS causas distintas, este diag las separa:

  (A) El asset EXISTE con cartera FCI pero `emisor` (y/o `ticker`) está en
      blanco → en el detalle sale "SIN EMISOR" / ticker vacío. Sí aparece en FCI
      y en TOTAL (cartera FCI). Fix = completar el catálogo `portafolio.assets`.

  (B) La tenencia FCI NO tiene fila en `portafolio.assets` (o la tiene con otra
      cartera) → el INNER JOIN la DEJA AFUERA del detalle FCI, pero el TOTAL la
      cuenta igual (LEFT JOIN → cartera 'OTROS'). Se detecta por el formato de
      `unidad` (contiene `CAFCI<n>-<m>`, que es un FCI aunque el asset no lo diga).

Mide todo contra la ÚLTIMA foto real (max fecha con aum='si'). No escribe nada.

Uso:  python -m scripts.diag_fci_vacios [YYYY-MM-DD]
      (sin fecha usa el último snapshot disponible)
"""
from __future__ import annotations

import sys

from psycopg.rows import dict_row

from core.postgres import get_pool

_FCI_CARTERAS = ("FCI", "CARTERA FCI")


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _fecha_snapshot(pedida: str | None) -> str | None:
    if pedida:
        r = _q("SELECT max(fecha) AS f FROM portafolio.tenencia "
               "WHERE aum='si' AND fecha <= %s", (pedida,))
    else:
        r = _q("SELECT max(fecha) AS f FROM portafolio.tenencia WHERE aum='si'")
    return r[0]["f"] if r and r[0]["f"] else None


def main() -> None:
    pedida = sys.argv[1] if len(sys.argv) > 1 else None
    fecha = _fecha_snapshot(pedida)
    if fecha is None:
        print("No hay snapshots con aum='si' en portafolio.tenencia. Nada que medir.")
        return
    print(f"== Foto AuM: {fecha}  (aum='si'){' · pedida '+pedida if pedida else ''} ==\n")

    # ── (A) Detalle FCI tal cual lo arma la vista, quedándonos con los NOMBRES VACÍOS ──
    # emisor vacío ('') o ticker vacío ('') en el asset con cartera FCI.
    filas_a = _q("""
        SELECT v.unidad,
               a.emisor       AS emisor_raw,
               a.ticker       AS ticker_raw,
               a.instrumento  AS instrumento_raw,
               a.cafci        AS cafci_raw,
               count(DISTINCT v.id_cuenta) AS n_cuentas,
               SUM(v.valuacion)            AS val_total
        FROM portafolio.tenencia v
        JOIN portafolio.assets a ON a.unidad = v.unidad
        WHERE v.fecha = %s AND v.aum = 'si'
          AND a.cartera = ANY(%s)
          AND (a.emisor IS NULL OR a.emisor = '' OR a.ticker IS NULL OR a.ticker = '')
        GROUP BY v.unidad, a.emisor, a.ticker, a.instrumento, a.cafci
        ORDER BY val_total DESC
    """, (fecha, list(_FCI_CARTERAS)))

    print("── (A) Assets con cartera FCI pero emisor/ticker VACÍO "
          "(salen en el detalle FCI y en TOTAL, con nombre en blanco) ──")
    if not filas_a:
        print("  (ninguno) — todos los assets FCI del snapshot tienen emisor Y ticker.\n")
    else:
        tot_a = 0.0
        for r in filas_a:
            val = float(r["val_total"] or 0)
            tot_a += val
            print(f"  {r['unidad']}")
            print(f"      emisor={r['emisor_raw']!r}  ticker={r['ticker_raw']!r}  "
                  f"instrumento={r['instrumento_raw']!r}  cafci={r['cafci_raw']!r}")
            print(f"      {r['n_cuentas']} cuenta(s) · valuación ${val:,.0f}")
        print(f"  → {len(filas_a)} unidad(es) FCI sin nombre · "
              f"valuación total ${tot_a:,.0f}\n")

    # ── (B) Tenencias FCI (por formato CAFCI de la unidad) SIN asset (o con otra cartera) ──
    # Estas NO aparecen en el detalle FCI (INNER JOIN las tira) pero SÍ en el TOTAL.
    filas_b = _q("""
        SELECT v.unidad,
               a.unidad     AS asset_match,
               a.cartera    AS cartera_asset,
               a.emisor     AS emisor_asset,
               count(DISTINCT v.id_cuenta) AS n_cuentas,
               SUM(v.valuacion)            AS val_total
        FROM portafolio.tenencia v
        LEFT JOIN portafolio.assets a ON a.unidad = v.unidad
        WHERE v.fecha = %s AND v.aum = 'si'
          AND v.unidad ~ 'CAFCI[0-9]+-[0-9]+'
          AND (a.unidad IS NULL OR a.cartera IS NULL OR a.cartera <> ALL(%s))
        GROUP BY v.unidad, a.unidad, a.cartera, a.emisor
        ORDER BY val_total DESC
    """, (fecha, list(_FCI_CARTERAS)))

    print("── (B) Tenencias con pinta de FCI (unidad tiene CAFCI…) pero SIN asset FCI "
          "(faltan en el detalle FCI; el TOTAL las cuenta como 'OTROS') ──")
    if not filas_b:
        print("  (ninguno) — toda tenencia con formato CAFCI tiene su asset con cartera FCI.\n")
    else:
        tot_b = 0.0
        for r in filas_b:
            val = float(r["val_total"] or 0)
            tot_b += val
            estado = ("SIN fila en assets" if r["asset_match"] is None
                      else f"asset con cartera={r['cartera_asset']!r} emisor={r['emisor_asset']!r}")
            print(f"  {r['unidad']}")
            print(f"      {estado}")
            print(f"      {r['n_cuentas']} cuenta(s) · valuación ${val:,.0f}")
        print(f"  → {len(filas_b)} unidad(es) FCI fuera del detalle · "
              f"valuación total ${tot_b:,.0f}\n")

    # ── Contexto: cuánto pesa el FCI del snapshot para dimensionar el problema ──
    ctx = _q("""
        SELECT count(DISTINCT v.unidad) AS n_unid, SUM(v.valuacion) AS val
        FROM portafolio.tenencia v
        JOIN portafolio.assets a ON a.unidad = v.unidad
        WHERE v.fecha = %s AND v.aum = 'si' AND a.cartera = ANY(%s)
    """, (fecha, list(_FCI_CARTERAS)))[0]
    print("── Contexto (universo FCI del detalle, snapshot actual) ──")
    print(f"  {ctx['n_unid']} unidades FCI · valuación total ${float(ctx['val'] or 0):,.0f}")
    print("\nListo. (A) = completar emisor/ticker en Manager→Assets. "
          "(B) = crear/recategorizar el asset como FCI.")


if __name__ == "__main__":
    main()
