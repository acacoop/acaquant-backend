"""diag_tipotitulo_aunesa.py — READ-ONLY: el `tipoTitulo` EXACTO que manda Aunesa
en la posición valuada, para saber por qué el writer NO dividió por 100.

Contexto (caso S13N6, 2026-07-24): el divisor del AuM lo decide
`jobs/aum.py::TIPOS_DIVISOR_100` contra el string `tipoTitulo` de Aunesa, que
NO se persiste en la base. El diag pega a Aunesa igual que el writer y muestra:
  1. Las filas que matchean el patrón (unidad + tipoTitulo textual).
  2. TODOS los tipoTitulo distintos de la cuenta, marcando cuáles NO están en
     TIPOS_DIVISOR_100 (candidatos a estar mal valuados si cotizan en paridad).

Uso:  python -m scripts.diag_tipotitulo_aunesa [cuenta] [patron]
      python -m scripts.diag_tipotitulo_aunesa 1116 S13N6     (defaults)
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date

from jobs.aum import TIPOS_DIVISOR_100, autenticar, consultar_posicion
from jobs.portafolio_backfill import _prox_habil


def main() -> None:
    cuenta = sys.argv[1] if len(sys.argv) > 1 else "1116"
    patron = (sys.argv[2] if len(sys.argv) > 2 else "S13N6").upper()

    desde = _prox_habil(date.today()).strftime("%d/%m/%Y")
    print(f"== posición valuada de la cuenta {cuenta} (desde={desde}) ==\n")
    headers = autenticar()
    data, reauth = consultar_posicion(cuenta, headers, desde)
    if reauth:
        headers = autenticar()
        data, _ = consultar_posicion(cuenta, headers, desde)
    if not isinstance(data, list):
        print(f"Respuesta inesperada de Aunesa: {type(data).__name__} — nada que mostrar.")
        return

    filas = [r for r in data if isinstance(r, dict) and r.get("informacion") == "Acumulado"]
    print(f"{len(filas)} filas 'Acumulado'.\n")

    print(f"── filas cuya unidad matchea {patron!r} ──")
    hubo = False
    for r in filas:
        unidad = str(r.get("unidad") or "")
        if patron in unidad.upper():
            hubo = True
            tipo = r.get("tipoTitulo")
            en_lista = str(tipo or "") in TIPOS_DIVISOR_100
            print(f"  unidad={unidad!r}")
            print(f"      tipoTitulo={tipo!r}")
            print(f"      ¿está en TIPOS_DIVISOR_100? → {'SÍ (divide ÷100)' if en_lista else 'NO → el writer NO divide (×1)'}")
    if not hubo:
        print(f"  (ninguna fila de la cuenta {cuenta} matchea — probá otra cuenta)")

    print("\n── todos los tipoTitulo de la cuenta (marcados los que NO dividen) ──")
    por_tipo: dict[str, list[str]] = defaultdict(list)
    for r in filas:
        por_tipo[str(r.get("tipoTitulo") or "(vacío)")].append(str(r.get("unidad") or ""))
    for tipo in sorted(por_tipo):
        unidades = por_tipo[tipo]
        marca = "  " if tipo in TIPOS_DIVISOR_100 else "⚠️ ×1 →"
        ej = ", ".join(unidades[:3]) + ("…" if len(unidades) > 3 else "")
        print(f"  {marca} {tipo!r}  ({len(unidades)} unidades: {ej})")

    print("\nLectura: el/los tipoTitulo con ⚠️ que sean renta fija en paridad hay")
    print("que sumarlos a TIPOS_DIVISOR_100 (jobs/aum.py) + corregir lo ya guardado.")


if __name__ == "__main__":
    main()
