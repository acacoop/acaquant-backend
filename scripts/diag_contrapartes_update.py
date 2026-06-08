"""scripts/diag_contrapartes_update.py [cuenta] — reproduce el "guardar" de la
segmentación de contrapartes (PATCH) directo en el backend, para ver el ERROR real
(el front lo muestra escondido en un tooltip).

Llama a api.services.contrapartes_seg.update_contraparte con los MISMOS valores que
el doc ya tiene → no cambia nada (idempotente). Si tira excepción, imprime el
traceback completo. Si devuelve {updated: False, ...}, muestra el motivo (ej. por
qué da 404 — no matchea la cuenta).

Uso (en el Droplet):
    python -m scripts.diag_contrapartes_update            # usa la primera contraparte
    python -m scripts.diag_contrapartes_update 818        # una cuenta puntual
"""
from __future__ import annotations

import sys
import traceback

from api.services import contrapartes_seg as m
from core.mongo import get_mongo_client_read


def main() -> int:
    arg = sys.argv[1].strip() if len(sys.argv) > 1 else None
    col = get_mongo_client_read()["CashFlow"]["Contrapartes"]

    if arg:
        doc = col.find_one(m._cuenta_match(arg), {"_id": 0, "cuenta": 1, "denominacion": 1,
                                                  "contraparte": 1, "segmento": 1})
    else:
        doc = col.find_one({"cuenta": {"$nin": [None, ""]}},
                           {"_id": 0, "cuenta": 1, "denominacion": 1,
                            "contraparte": 1, "segmento": 1})
    if not doc:
        print(f"No encontré contraparte para cuenta={arg!r}. Probá con otra.")
        return 1

    cuenta = str(doc.get("cuenta"))
    print(f"Doc a re-guardar (sin cambios): {doc}\n")
    print(f"_cuenta_match({cuenta!r}) = {m._cuenta_match(cuenta)}\n")

    try:
        res = m.update_contraparte(
            cuenta=cuenta,
            contraparte=doc.get("contraparte"),
            segmento=doc.get("segmento"),
            actor="diag",
        )
        print(f"RESULTADO: {res}")
        if not res.get("updated"):
            print(f"  ⚠ NO actualizó. Motivo: {res.get('reason')!r} "
                  f"(reason=not_found → el router devuelve 404; sin_campos → 400).")
        else:
            print("  ✓ OK — el backend guarda bien. Si el front da error, es del lado HTTP/auth.")
    except Exception:
        print("✖ EXCEPCIÓN al guardar (este es el error real):\n")
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
