"""Test mínimo de 1 request a Aunesa para descartar nuestro código.

Hace UNA SOLA llamada a `/posicionValuada` con la fecha y cuenta que pases,
y mide cuánto tarda. Sin paralelismo, sin retries, sin nada.

Útil para descartar:
  - ¿Aunesa contesta? ¿En cuánto tiempo?
  - ¿Mi cuenta de testeo da datos para esa fecha?
  - ¿Cambia el comportamiento si subo el timeout?

Comparar contra una request "del día corriente" para ver si la diferencia
es la fecha (Aunesa) o nuestro código.

Uso:
    # 1 cuenta chica, fecha pasada
    python -m scripts.test_aunesa_call 805 03/07/2025 240

    # Misma cuenta, fecha de hoy (T+2 de hoy)
    python -m scripts.test_aunesa_call 805 08/05/2026 240

    # Cuenta grande problemática, fecha pasada
    python -m scripts.test_aunesa_call 101 03/07/2025 360
"""
from __future__ import annotations

import sys
import time

from jobs.aum import autenticar, consultar_posicion


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.test_aunesa_call <id_cuenta> [desde DD/MM/YYYY] [timeout_s]")
        sys.exit(1)

    cid = sys.argv[1]
    desde = sys.argv[2] if len(sys.argv) > 2 else "08/05/2026"
    timeout = int(sys.argv[3]) if len(sys.argv) > 3 else 240

    print(f"Cuenta:  {cid}")
    print(f"desde:   {desde}")
    print(f"timeout: {timeout}s")

    print("\nAutenticando…", flush=True)
    t_auth = time.time()
    h = autenticar()
    print(f"Auth OK ({time.time() - t_auth:.1f}s)\n", flush=True)

    print("Pegando a /posicionValuada…", flush=True)
    t0 = time.time()
    try:
        data, reauth = consultar_posicion(cid, h, desde, timeout=timeout)
        dt = time.time() - t0
        n = len(data) if data else 0
        size_kb = sum(len(str(d)) for d in (data or [])) / 1024
        print(f"\n✅ OK en {dt:.1f}s")
        print(f"   items devueltos:  {n}")
        print(f"   payload aprox:    {size_kb:.1f} KB")
        print(f"   reauth necesario: {reauth}")
        if n > 0:
            sample = data[0]
            print("\n   primer item (sample):")
            for k, v in sample.items():
                print(f"     {k}: {v}")
    except Exception as e:
        dt = time.time() - t0
        print(f"\n❌ FAIL en {dt:.1f}s")
        print(f"   {type(e).__name__}: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
