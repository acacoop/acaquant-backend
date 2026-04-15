"""
check_aum_raw.py — Diagnóstico: muestra los datos RAW de Aunesa para una cuenta
buscando posiciones que contengan una palabra clave (ej: "MAX").

Uso:
    python check_aum_raw.py MAX          # busca "MAX" en todos los campos
    python check_aum_raw.py MAX 004      # busca "MAX" solo en cuenta 004
"""

import sys
import os
import requests
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import config
from jobs.aum import autenticar, obtener_cuentas, consultar_posicion, fecha_t2

KEYWORD  = sys.argv[1].upper() if len(sys.argv) > 1 else "MAX"
CUENTA_F = sys.argv[2] if len(sys.argv) > 2 else None

def main():
    print(f"Buscando '{KEYWORD}' en datos crudos de Aunesa...\n")
    headers = autenticar()
    cuentas = obtener_cuentas(headers)
    desde   = fecha_t2()

    if CUENTA_F:
        cuentas = cuentas[cuentas["id"].astype(str) == str(CUENTA_F)]
        print(f"Filtrando solo cuenta {CUENTA_F} ({len(cuentas)} encontrada)\n")

    for _, row in cuentas.iterrows():
        cuenta_id    = str(row["id"])
        denominacion = row["denominacion"]

        data, _ = consultar_posicion(cuenta_id, headers, desde)
        if not data:
            continue

        df = pd.DataFrame(data)

        # Buscar keyword en cualquier campo string
        mask = pd.Series([False] * len(df))
        for col in df.columns:
            try:
                mask |= df[col].astype(str).str.contains(KEYWORD, case=False, na=False)
            except Exception:
                pass

        matches = df[mask]
        if matches.empty:
            continue

        print(f"{'='*60}")
        print(f"Cuenta [{cuenta_id}] {denominacion}")
        print(f"  → {len(matches)} filas con '{KEYWORD}'\n")

        for _, r in matches.iterrows():
            print(f"  informacion : {r.get('informacion', 'N/A')}")
            print(f"  unidad      : {r.get('unidad', 'N/A')}")
            print(f"  cuenta      : {r.get('cuenta', 'N/A')}")
            print(f"  cantidad    : {r.get('cantidad', 'N/A')}")
            print(f"  precio      : {r.get('precio', 'N/A')}")
            print(f"  tipoTitulo  : {r.get('tipoTitulo', 'N/A')}")
            print()

        # Mostrar todos los valores únicos de 'informacion' para esta cuenta
        if "informacion" in df.columns:
            print(f"  Valores únicos de 'informacion' en esta cuenta: {sorted(df['informacion'].dropna().unique())}")
        print()

if __name__ == "__main__":
    main()
