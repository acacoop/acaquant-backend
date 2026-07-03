"""scripts/diag_tesoreria.py — timing + resultado del service de Tesorería, SERVER-SIDE.

Corre en el Droplet (sin pasar por Vercel/Cloudflare). Mide cuánto tarda
`ingresos_egresos_dia` (incluye el GET en vivo a Aunesa) y qué devuelve, para distinguir:
  • el endpoint anda pero es LENTO  → el 502 es timeout del proxy (Vercel/CF/nginx).
  • Aunesa devuelve VACÍO / ERROR   → es tema de datos/permiso, no de timeout.

No imprime montos (solo conteos + timing) → salida segura para pegar en el chat.

Uso:
  python -m scripts.diag_tesoreria
  python -m scripts.diag_tesoreria --fecha 2026-07-03 --estado Procesado
"""
from __future__ import annotations

import argparse
import sys
import time

from api.services import tesoreria


def main() -> int:
    p = argparse.ArgumentParser(description="Diag timing del service de Tesorería.")
    p.add_argument("--fecha", default=None, help="ISO YYYY-MM-DD (default hoy ART)")
    p.add_argument("--estado", default="Procesado")
    args = p.parse_args()

    print(f"Llamando ingresos_egresos_dia(fecha={args.fecha}, estado={args.estado!r})…")
    t0 = time.perf_counter()
    try:
        out = tesoreria.ingresos_egresos_dia(fecha=args.fecha, estado=args.estado)
    except Exception as e:
        dt = time.perf_counter() - t0
        print(f"✗ EXCEPCIÓN tras {dt:.1f}s → {type(e).__name__}: {str(e)[:300]}")
        print("  (una excepción acá = por qué el endpoint tira 5xx; suele ser Aunesa no-200)")
        return 1

    dt = time.perf_counter() - t0
    print(f"✓ OK en {dt:.2f}s")
    print(f"  fecha = {out['fecha']}   estado = {out['estado']}")
    print(f"  raw (filas crudas de Aunesa) = {out['raw']}")
    print(f"  n   (ingresos + egresos)     = {out['n']}")
    for uni, b in out["resumen"].items():
        print(f"    {uni}: {b['n']} movimientos")
    if dt > 9:
        print(f"  ⚠ {dt:.1f}s es LENTO → probable causa del 502: timeout del proxy "
              "(Vercel Hobby ~10s / nginx / CF). Hay que servirlo async o cachear.")
    elif out["raw"] == 0:
        print("  ⚠ Aunesa devolvió 0 filas crudas → revisar fecha/estado/permiso del web service.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
