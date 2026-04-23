"""Verifica qué devuelve get_historico_curva('soberanos') después del backfill.

Sin pasar por la API, llama el service directo (mismo código que sirve
el endpoint /api/cotizaciones/historico/curva).
"""
from __future__ import annotations

from collections import Counter

from api.services.renta_fija import get_historico_curva


def main() -> int:
    rows = get_historico_curva(curva="soberanos")
    if not rows:
        print("VACÍO — el endpoint no devuelve nada.")
        return 1

    fechas = sorted({r["fecha"] for r in rows})
    tickers = Counter(r["ticker"] for r in rows)
    con_tea = sum(1 for r in rows if r.get("TEA") is not None)
    con_dur = sum(1 for r in rows if r.get("duration") is not None)

    print(f"Total rows:    {len(rows)}")
    print(f"Fechas únicas: {len(fechas)}  ({fechas[0]} → {fechas[-1]})")
    print(f"con TEA:       {con_tea}")
    print(f"con duration:  {con_dur}")
    print()
    print("Por ticker:")
    for tk, n in sorted(tickers.items()):
        print(f"  {tk:<10} {n}")
    print()
    print("Primeras 3 fechas:", fechas[:3])
    print("Últimas 3 fechas: ", fechas[-3:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
