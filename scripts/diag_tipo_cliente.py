"""diag_tipo_cliente.py — read-only de Clientes.Comitentes.tipo_cliente.

Imprime los valores únicos del campo `tipo_cliente` con su count, ordenado
descendente. Sirve para entender qué valores toma hoy (PH/PJ, etc.) antes de
usarlo en la lógica de segmentación patrimonial
(docs/SEGMENTACION_PATRIMONIAL.md).

Uso:
    python -m scripts.diag_tipo_cliente
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client_read

# Consola Windows (cp1252) revienta con acentos; forzamos UTF-8 si se puede.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    col = get_mongo_client_read()["Clientes"]["Comitentes"]

    total = col.estimated_document_count()
    pipeline = [
        {"$group": {"_id": "$tipo_cliente", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]
    rows = list(col.aggregate(pipeline))

    print(f"Clientes.Comitentes — {total} docs totales.")
    print(f"Valores únicos de tipo_cliente: {len(rows)}")
    print("-" * 60)
    print(f"{'count':>8}  tipo_cliente")
    print("-" * 60)
    for r in rows:
        val = r["_id"]
        # Diferenciar null vs "" vs string real para que se vea.
        if val is None:
            disp = "<NULL>"
        elif val == "":
            disp = "<STRING VACIO>"
        else:
            disp = repr(val)
        print(f"{r['n']:>8}  {disp}")


if __name__ == "__main__":
    main()
