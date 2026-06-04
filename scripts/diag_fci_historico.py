"""scripts/diag_fci_historico.py — estado HISTÓRICO de las suscripciones FCI en
Operaciones, por año. ¿Están presentes y con bruto correcto, rotas ($0), o faltan?

El dry-run del backfill mostró solo ~101 rotas (recientes) y 0 en la historia vieja.
Eso puede ser (a) la historia ya corregida por el --full de la tarde, o (b) las
suscripciones viejas NO están en Operaciones. Este diag (read-only) lo distingue.

Por año cuenta, de las suscripciones FCI normales (categoria suscripcion_fci,
comprobante BOL) de NegocioMovimientos:
  - presentes+OK   : están en Operaciones con bruto > 0
  - presentes+ROTAS: están en Operaciones con bruto 0/null (las arregla el backfill)
  - FALTANTES      : no están en Operaciones (otro problema: habría que inyectarlas)

Read-only. Correr:
    python -m scripts.diag_fci_historico
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def main() -> int:
    db = get_mongo_client_read()["CashFlow"]
    mov, ops = db["NegocioMovimientos"], db["Operaciones"]

    print(f"  {'año':6} {'NegocioMov':>11} {'present+OK':>11} {'present+ROTAS':>13} {'FALTAN':>8}")
    tot = {"neg": 0, "ok": 0, "rotas": 0, "faltan": 0}
    for anio in range(2022, 2027):
        desde, hasta = f"{anio}-01-01", f"{anio + 1}-01-01"
        neg = {
            str(d["comprobante"]).strip()
            for d in mov.find(
                {"categoria": "suscripcion_fci",
                 "comprobante": {"$regex": "^BOL", "$options": "i"},
                 "fecha": {"$gte": desde, "$lt": hasta}},
                {"_id": 0, "comprobante": 1})
            if d.get("comprobante")
        }
        if not neg:
            continue
        boletos = list(neg)
        present = ops.count_documents({"boleto": {"$in": boletos}})
        rotas = ops.count_documents({"boleto": {"$in": boletos}, "bruto": {"$in": [0, None]}})
        ok = present - rotas
        faltan = len(neg) - present
        print(f"  {anio:<6} {len(neg):>11,} {ok:>11,} {rotas:>13,} {faltan:>8,}")
        tot["neg"] += len(neg); tot["ok"] += ok; tot["rotas"] += rotas; tot["faltan"] += faltan

    print(f"  {'TOTAL':6} {tot['neg']:>11,} {tot['ok']:>11,} {tot['rotas']:>13,} {tot['faltan']:>8,}")
    print()
    if tot["faltan"]:
        print(f"⚠ {tot['faltan']:,} suscripciones FCI NO están en Operaciones → el backfill de "
              "bruto NO las trae (solo corrige las que están). Habría que INYECTARLAS desde "
              "NegocioMov (otro paso). Decidir si hace falta para el histórico.")
    if tot["rotas"]:
        print(f"→ {tot['rotas']:,} presentes pero rotas ($0) → las arregla scripts.backfill_fci_bruto.")
    if not tot["faltan"] and not tot["rotas"]:
        print("✓ Todas las suscripciones FCI históricas están presentes y con bruto OK. "
              "El histórico ya está sano (lo corrigió el --full).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
