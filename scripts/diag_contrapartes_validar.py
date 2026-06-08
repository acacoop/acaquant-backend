"""scripts/diag_contrapartes_validar.py [término] — muestra el TIPO de cada campo
de las contrapartes que matchean un término (read-only).

Sirve para confirmar la causa del "da error al guardar" en algunas filas: si
`contraparte` o `cuenta` está guardado como NÚMERO (int/float) en vez de string
(ej. CUIT de sociedades gerentes), el front lo reenvía como número y la validación
del endpoint (str) lo rechaza con 422. Esto se arregló coercionando a string.

Uso (en el Droplet):
    python -m scripts.diag_contrapartes_validar ADCAP
    python -m scripts.diag_contrapartes_validar 30710198388
    python -m scripts.diag_contrapartes_validar            # primeras 10
"""
from __future__ import annotations

import re
import sys

from core.mongo import get_mongo_client_read

_CAMPOS = ("cuenta", "denominacion", "contraparte", "segmento")


def main() -> int:
    term = " ".join(sys.argv[1:]).strip()
    col = get_mongo_client_read()["CashFlow"]["Contrapartes"]

    if term:
        rgx = {"$regex": re.escape(term), "$options": "i"}
        q: dict = {"$or": [{"denominacion": rgx}, {"cuenta": rgx}, {"contraparte": rgx}]}
    else:
        q = {"cuenta": {"$nin": [None, ""]}}

    docs = list(col.find(q, {"_id": 0, **{c: 1 for c in _CAMPOS}}).limit(10))
    print(f"{len(docs)} doc(s) para término={term!r}:\n")
    sospechosos = 0
    for d in docs:
        for f in _CAMPOS:
            v = d.get(f)
            tipo = type(v).__name__
            flag = "  ⚠ NO es string" if v is not None and not isinstance(v, str) else ""
            if flag:
                sospechosos += 1
            print(f"  {f:14} = {v!r:40}  (tipo: {tipo}){flag}")
        print()

    if sospechosos:
        print(f"⚠ {sospechosos} campo(s) NO-string → esa era la causa del 422. El fix "
              "(coercionar a string en listar + coerce_numbers_to_str) lo resuelve.")
    else:
        print("Todos los campos son string. Si igual da error, pegame el status del log/banner.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
