"""diag_aunesa_posicion_cruda.py — respuesta CRUDA y COMPLETA de Aunesa.

El job de AuM (jobs/aum.py, aum_backfill.py) pide posicionValuada con
`desde = T+2 hábil` de la fecha y luego procesa SOLO los items con
`informacion == "Acumulado"` — el resto se descarta.

Este script pega a Aunesa con la MISMA lógica del job, pero muestra
TODO lo que vuelve: cuántos items, todos los valores de `informacion`
(con su conteo), todos los campos, y un ejemplo de cada tipo de
`informacion`. Sirve para entender qué se está pidiendo y qué se tira.

NO escribe nada — solo consulta y muestra.

Corre:  python -m scripts.diag_aunesa_posicion_cruda <id_cuenta> <fecha YYYY-MM-DD>
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime

from jobs.aum import autenticar, consultar_posicion
from jobs.aum_backfill import t2_para_fecha


def main() -> None:
    if len(sys.argv) < 3:
        print("Uso: python -m scripts.diag_aunesa_posicion_cruda "
              "<id_cuenta> <fecha YYYY-MM-DD>")
        return
    id_cuenta = sys.argv[1]
    fecha_str = sys.argv[2]
    try:
        fecha_dt = datetime.strptime(fecha_str, "%Y-%m-%d")
    except ValueError:
        print(f"fecha mal formada: {fecha_str} (esperado YYYY-MM-DD)")
        return

    desde = t2_para_fecha(fecha_dt)
    print("=" * 80)
    print(f"id_cuenta:       {id_cuenta}")
    print(f"fecha_snapshot:  {fecha_str}")
    print(f"desde (T+2 que el job le manda a Aunesa): {desde}")
    print("=" * 80)

    headers = autenticar()
    data, reauth = consultar_posicion(id_cuenta, headers, desde)
    if reauth:
        headers = autenticar()
        data, _ = consultar_posicion(id_cuenta, headers, desde)
    if data is None:
        print("Aunesa devolvió None / respuesta no-200.")
        return

    items = data if isinstance(data, list) else []
    print(f"\nItems totales en la respuesta: {len(items)}")

    # ── Valores de 'informacion' (el job solo procesa 'Acumulado') ──────
    por_info: Counter = Counter(str(r.get("informacion")) for r in items)
    print(f"\nValores de 'informacion' (el job SOLO procesa 'Acumulado'):")
    for info, n in por_info.most_common():
        marca = "  <- lo que el job usa" if info == "Acumulado" else "  (descartado)"
        print(f"  {n:6d}  {info!r}{marca}")

    # ── Campos presentes ────────────────────────────────────────────────
    campos: set[str] = set()
    for r in items:
        campos.update(r.keys())
    print(f"\nCampos que trae cada item:\n  {sorted(campos)}")

    # ── Muestra: un item de cada 'informacion' ──────────────────────────
    print("\n" + "=" * 80)
    print("MUESTRA — primer item de cada 'informacion':")
    vistos: set[str] = set()
    for r in items:
        info = str(r.get("informacion"))
        if info in vistos:
            continue
        vistos.add(info)
        print(f"\n--- informacion = {info!r} ---")
        for k in sorted(r.keys()):
            print(f"   {k}: {r[k]!r}")


if __name__ == "__main__":
    main()
