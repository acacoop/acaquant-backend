"""diag_parse_ops_excel.py — verifica cómo queda parseada una fila del Excel de
operaciones ANTES de subirla (REGLA #2: no asumir que el parser la lee bien).

Read-only, no toca la base. Uso:
    python -m scripts.diag_parse_ops_excel                 # muestra fija de ejemplo
    python -m scripts.diag_parse_ops_excel <archivo.xlsx>  # las 5 primeras filas reales
"""
from __future__ import annotations

import sys

from api.services.operaciones_informes import normalizar_fila

_MUESTRA = [
    {"boleto": "BOL 2025099355", "concertacion": "1/7/2025", "id_cuenta": "1178",
     "denominacion": "IAM FCI DINAMICO ABIERTO PYMES", "bruto": "26404857,25",
     "arancel": "ARS 48328.77", "instrumento": "[*ARP290800154] Nro. 8664 Vto. 02/09/2025",
     "tipo_operacion": "ECHEQ - Compra", "condiciones": "ARS Inm", "tasa": "35"},
    {"boleto": "BOL 2025099359", "concertacion": "1/7/2025", "id_cuenta": "437",
     "denominacion": "SANCHEZ MARIO ALBERTO Y SANCHEZ JOSE DAVID SH", "bruto": "18860612,32",
     "arancel": "ARS 34520.55", "instrumento": "[*ARP290800153] Nro. 8663 Vto. 02/09/2025",
     "tipo_operacion": "ECHEQ - Subasta", "condiciones": "ARS Inm", "tasa": "35"},
]


def _filas() -> list[dict]:
    if len(sys.argv) < 2:
        return _MUESTRA
    import pandas as pd
    df = pd.read_excel(sys.argv[1], dtype=str).head(5)
    return df.where(df.notna(), None).to_dict("records")


def main() -> None:
    for i, row in enumerate(_filas(), 1):
        doc = normalizar_fila(row)
        print(f"\n── fila {i} " + "─" * 50)
        if doc is None:
            print("  ✗ DESCARTADA (sin boleto)")
            continue
        for k, v in doc.items():
            flag = "  " if v is not None else "  ← VACÍO"
            print(f"  {k:<15} = {v!r}{flag}")


if __name__ == "__main__":
    main()
