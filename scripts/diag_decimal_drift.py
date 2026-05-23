"""Mide el drift de redondeo float vs Decimal en los totales de AuM.

Read-only. Responde la pregunta de EXT-MONEY1 ANTES de tocar el motor:
¿cuánto difiere realmente sumar las posiciones de una cuenta en float
(lo que hace hoy) vs en Decimal? Si el drift es ~$0.00, migrar el motor a
Decimal no vale el riesgo. Si es material (pesos), lo justifica.

Suma las `valuacion` del último snapshot de `Valuaciones.AuM` agrupando por
`id_cuenta`, en float y en Decimal sobre los MISMOS valores almacenados, y
reporta el peor caso y el agregado.

Nota: mide el error de ACUMULACIÓN en la suma. No captura el error de
ingesta (los valores ya vienen float de Mongo); es una cota inferior del
drift total, pero es exactamente el escenario "sumás miles de boletos".

Uso (en el Droplet):
    python -m scripts.diag_decimal_drift
    python -m scripts.diag_decimal_drift --top 20   # más cuentas en el detalle
"""
from __future__ import annotations

import sys
from collections import defaultdict
from decimal import Decimal

from core.mongo import get_mongo_client_read


def main() -> int:
    top = 10
    if "--top" in sys.argv:
        try:
            top = int(sys.argv[sys.argv.index("--top") + 1])
        except (IndexError, ValueError):
            pass

    col = get_mongo_client_read()["Valuaciones"]["AuM"]

    ult = col.find_one({}, {"fecha_snapshot": 1, "_id": 0}, sort=[("fecha_snapshot", -1)])
    if not ult:
        print("Valuaciones.AuM vacía — nada para medir.")
        return 0
    fecha = ult["fecha_snapshot"]
    print(f"Snapshot: {fecha}")

    float_por_cta: dict[str, float] = defaultdict(float)
    dec_por_cta: dict[str, Decimal] = defaultdict(Decimal)
    n = 0
    for doc in col.find(
        {"fecha_snapshot": fecha},
        {"id_cuenta": 1, "valuacion": 1, "_id": 0},
    ):
        cta = str(doc.get("id_cuenta"))
        v = doc.get("valuacion")
        if v is None:
            continue
        float_por_cta[cta] += float(v)
        # str(v) preserva la representación decimal del float almacenado
        dec_por_cta[cta] += Decimal(str(v))
        n += 1

    drifts: list[tuple[str, float]] = []
    for cta in float_por_cta:
        f = float_por_cta[cta]
        d = float(dec_por_cta[cta])
        drifts.append((cta, abs(f - d)))
    drifts.sort(key=lambda x: x[1], reverse=True)

    total_float = sum(float_por_cta.values())
    total_dec = float(sum(dec_por_cta.values()))
    materiales = [x for x in drifts if x[1] >= 0.01]

    print(f"Posiciones: {n} · cuentas: {len(float_por_cta)}")
    print(f"Total AuM (float):   {total_float:,.6f}")
    print(f"Total AuM (Decimal): {total_dec:,.6f}")
    print(f"Drift total:         {abs(total_float - total_dec):,.6f}")
    print(f"Cuentas con drift ≥ $0.01: {len(materiales)}")
    print(f"\nTop {top} cuentas por drift:")
    for cta, dr in drifts[:top]:
        print(f"  cuenta {cta:>10} → drift {dr:.6f}")

    print(
        "\nVeredicto: si el drift total y el peor caso son < $0.01, "
        "el motor en float es contablemente suficiente y NO conviene "
        "el riesgo de migrar a Decimal. Si hay pesos, justifica el refactor."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
