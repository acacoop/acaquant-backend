"""diag_descalce_flujo_aum.py — alineamiento serie de valor AuM vs flujos.

Para una cuenta imprime, día por día: el valor total del portfolio
(suma de Valuaciones.AuM de ese fecha_snapshot) y los flujos externos
(CashFlow.NegocioMovimientos) de ese día.

Sirve para detectar el descalce que rompe el rendimiento mensual: un
depósito/extracción que cae el mismo día (o cerca) del snapshot de
cierre de mes y que el snapshot puede o no haber capturado.

Qué mirar: en el cambio de mes, si un depósito del último día del mes
se ve como salto en el valor de ESE día o recién en el snapshot
SIGUIENTE. Si el salto aparece un día tarde -> el cierre de mes está
descalzado respecto del flujo.

NO modifica nada. Corre:
    python -m scripts.diag_descalce_flujo_aum <id_cuenta>
"""
from __future__ import annotations

import sys
from collections import defaultdict

from core.mongo import get_mongo_client

_FLUJOS = {"deposito", "transferencia", "extraccion"}


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.diag_descalce_flujo_aum <id_cuenta>")
        return
    id_cuenta = sys.argv[1]
    cli = get_mongo_client()
    db_val = cli["Valuaciones"]
    db_cf = cli["CashFlow"]

    # Serie diaria de valor: suma de Valuaciones.AuM por fecha_snapshot.
    pipeline = [
        {"$match": {"id_cuenta": id_cuenta}},
        {"$group": {
            "_id":   "$fecha_snapshot",
            "valor": {"$sum": "$valuacion"},
            "n":     {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]
    serie: dict[str, tuple[float, int]] = {
        str(r["_id"]): (float(r.get("valor") or 0), int(r.get("n") or 0))
        for r in db_val["AuM"].aggregate(pipeline)
    }

    # Flujos externos por fecha.
    flujos: dict[str, list[dict]] = defaultdict(list)
    for m in db_cf["NegocioMovimientos"].find(
        {
            "cuenta":    {"$regex": f"^\\[{id_cuenta}\\]"},
            "categoria": {"$in": list(_FLUJOS)},
        },
        {"_id": 0, "fecha": 1, "categoria": 1, "importe": 1, "moneda": 1},
    ):
        f = m.get("fecha")
        if isinstance(f, str):
            flujos[f[:10]].append(m)

    n_flujos = sum(len(v) for v in flujos.values())
    todas = sorted(set(serie) | set(flujos))
    print(f"id_cuenta={id_cuenta}  —  {len(serie)} snapshots de AuM, {n_flujos} flujos")
    print("=" * 84)
    print(f"{'FECHA':12} {'VALOR AuM':>20} {'ndocs':>6}   {'Δ vs dia previo':>18}   FLUJOS DEL DIA")
    print("-" * 84)

    mes_prev: str | None = None
    valor_prev: float | None = None
    for f in todas:
        mes = f[:7]
        if mes_prev and mes != mes_prev:
            print(f"   {'·' * 30}  cambio de mes  {mes_prev} -> {mes}")
        mes_prev = mes

        valor_n = serie.get(f)
        if valor_n is not None:
            valor, n = valor_n
            valor_s = f"{valor:,.0f}"
            delta_s = f"{valor - valor_prev:+,.0f}" if valor_prev is not None else "—"
            valor_prev = valor
            n_s = str(n)
        else:
            valor_s = "—"
            delta_s = "(sin snapshot)"
            n_s = "—"

        fl = flujos.get(f, [])
        fl_s = ""
        if fl:
            partes = []
            for m in fl:
                imp = float(m.get("importe") or 0)
                partes.append(f"{m.get('categoria')} {imp:+,.0f} {m.get('moneda') or ''}".strip())
            fl_s = "  |  ".join(partes)

        print(f"{f:12} {valor_s:>20} {n_s:>6}   {delta_s:>18}   {fl_s}")

    print("=" * 84)
    print("Lectura: si un deposito del ultimo dia del mes NO aparece reflejado en el")
    print("'VALOR AuM' de ese dia pero si en el snapshot siguiente, el cierre de mes")
    print("esta descalzado respecto del flujo -> el rendimiento de ese mes esta mal.")


if __name__ == "__main__":
    main()
