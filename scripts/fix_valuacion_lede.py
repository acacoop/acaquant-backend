"""fix_valuacion_lede.py — corrige en portafolio.tenencia las valuaciones que el
writer guardó SIN dividir por 100 (caso tipoTitulo 'LEDE', ej. [9416] S13N6).

Causa (medida 2026-07-24 con diag_tipotitulo_aunesa): Aunesa manda las letras a
descuento con tipoTitulo='LEDE', que no estaba en TIPOS_DIVISOR_100 → el writer
valuó precio×cantidad sin ÷100 → AuM inflado ×100. El tipo ya se agregó a las
listas (jobs/aum.py, jobs/partner_export.py, api/services/pnl.py) — esto arregla
la HISTORIA ya escrita.

Scope (REGLA #4): SOLO filas de la unidad indicada donde el divisor aplicado fue
×1 (cociente valuacion/(precio×cantidad) ≈ 1, mismo criterio del diag). Tras la
corrección el cociente queda ≈0.01 → re-correrlo no vuelve a tocar nada
(idempotente). Es un UPDATE chico por índice de unidad, no un scan.

Además AUDITA (solo lista, no toca) otras unidades de cartera renta fija
(HD/DL/ARS) con el mismo síntoma, por si hay más LEDEs u otros tipos nuevos.

Uso:
    python -m scripts.fix_valuacion_lede               # DRY-RUN de S13N6
    python -m scripts.fix_valuacion_lede --apply       # corrige S13N6
    python -m scripts.fix_valuacion_lede S28F7 --apply # otra unidad puntual
"""
from __future__ import annotations

import sys

from psycopg.rows import dict_row

from core.postgres import get_pool

# Cociente valuacion/(precio×cantidad) ≈ 1 → el writer NO dividió.
_COND_X1 = ("precio <> 0 AND cantidad <> 0 "
            "AND abs(valuacion / (precio * cantidad) - 1) < 0.05")


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv[1:]
    patron = args[0] if args else "S13N6"
    like = f"%{patron}%"

    filas = _q(
        f"SELECT fecha, id_cuenta, cantidad, precio, valuacion FROM portafolio.tenencia "
        f"WHERE unidad ILIKE %s AND {_COND_X1} ORDER BY fecha, id_cuenta", (like,))
    print(f"== unidad ~ {patron!r}: {len(filas)} filas guardadas SIN ÷100 ==")
    total_antes = sum(float(f["valuacion"] or 0) for f in filas)
    fechas = sorted({str(f["fecha"]) for f in filas})
    if filas:
        print(f"   fechas: {fechas[0]} → {fechas[-1]} ({len(fechas)} días) · "
              f"suma valuación actual ${total_antes:,.0f} → quedaría ${total_antes / 100:,.0f}")
        for f in filas[-8:]:
            print(f"   {f['fecha']}  cta {f['id_cuenta']:<6} "
                  f"${float(f['valuacion']):>20,.2f} → ${float(f['valuacion']) / 100:>18,.2f}")
        if len(filas) > 8:
            print(f"   … (mostradas las últimas 8 de {len(filas)})")

    # Auditoría informativa: otras unidades renta fija con el mismo síntoma.
    otros = _q(
        f"SELECT unidad, count(*) AS n, max(fecha) AS ult, sum(valuacion) AS val "
        f"FROM portafolio.tenencia "
        f"WHERE cartera IN ('HD','DL','ARS') AND unidad NOT ILIKE %s AND {_COND_X1} "
        f"GROUP BY unidad ORDER BY val DESC LIMIT 30", (like,))
    print(f"\n── AUDIT (no se toca): otras unidades RF (HD/DL/ARS) valuadas ×1 ({len(otros)}) ──")
    for o in otros:
        print(f"   {o['unidad']:<45} {o['n']:>5} filas · últ {o['ult']} · "
              f"${float(o['val'] or 0):,.0f}")
    if otros:
        print("   → si alguna cotiza en paridad, correr este fix con esa unidad y/o")
        print("     medir su tipoTitulo con diag_tipotitulo_aunesa.")

    if not filas:
        print("\nNada que corregir para ese patrón.")
        return 0
    if not apply:
        print(f"\nDRY-RUN. Con --apply se dividen por 100 las {len(filas)} filas de arriba.")
        return 0

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE portafolio.tenencia SET valuacion = valuacion / 100 "
            f"WHERE unidad ILIKE %s AND {_COND_X1}", (like,))
        n = cur.rowcount
        conn.commit()
    print(f"\n✅ Corregidas {n} filas (valuacion ÷ 100).")
    print("   El AuM las muestra bien ya; los TOTALES precomputados (pnl_totales_cache/")
    print("   consolidado) se refrescan solos en la próxima corrida de sus crons.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
