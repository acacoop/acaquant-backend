"""Diag READ-ONLY: pipeline FCI bilateral — dónde se corta (NegocioMov → Operaciones).

jobs/fci_bilateral lee las suscripciones/rescates FCI de CashFlow.NegocioMovimientos
(categoría LIQ + comprobante ^CL; categoría SOL = DOC) y las lleva a
CashFlow.Operaciones con `etapa`. Si en la vista OPERACIONES no aparecen, este diag
muestra DÓNDE está el corte: ¿hay FCI en NegocioMov? ¿llegaron a Operaciones?
¿solo los últimos 10 días (falta --full)?

Uso:
    python -m scripts.diag_fci_pipeline
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read

_CATS_LIQ = ("suscripcion_fci", "rescate_fci")
_CATS_SOL = ("solicitud_suscripcion_fci", "solicitud_rescate_fci")
_MERCADO = "FCI Bilateral"


def main() -> int:
    cli = get_mongo_client_read()
    mov = cli["CashFlow"]["NegocioMovimientos"]
    ops = cli["CashFlow"]["Operaciones"]
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()
    d10 = (hoy - timedelta(days=10)).isoformat()
    d90 = (hoy - timedelta(days=90)).isoformat()

    print("== NegocioMovimientos — categorías FCI (total / últimos 90d / 10d) ==")
    for cat in (*_CATS_LIQ, *_CATS_SOL):
        tot = mov.count_documents({"categoria": cat})
        u90 = mov.count_documents({"categoria": cat, "fecha": {"$gte": d90}})
        u10 = mov.count_documents({"categoria": cat, "fecha": {"$gte": d10}})
        print(f"  {cat:<28} total={tot:>7}  90d={u90:>6}  10d={u10:>5}")

    print("\n== LIQ: comprobante por prefijo (el job sólo lee ^CL; ^BOL ya entran como boleto) ==")
    for cat in _CATS_LIQ:
        for pref in ("CL", "BOL"):
            n = mov.count_documents({"categoria": cat,
                                     "comprobante": {"$regex": f"^{pref}", "$options": "i"}})
            print(f"  {cat:<20} ^{pref:<4} {n}")
        sinc = mov.count_documents({"categoria": cat,
                                    "$or": [{"comprobante": None}, {"comprobante": ""}]})
        print(f"  {cat:<20} sin comprobante {sinc}")

    print(f"\n== Operaciones — mercado='{_MERCADO}' (lo que el job DEBERÍA haber escrito) ==")
    print(f"  total: {ops.count_documents({'mercado': _MERCADO})}")
    for d in ops.aggregate([
        {"$match": {"mercado": _MERCADO}},
        {"$group": {"_id": {"operacion": "$operacion", "etapa": "$etapa"},
                    "n": {"$sum": 1},
                    "min_f": {"$min": "$concertacion"}, "max_f": {"$max": "$concertacion"}}},
        {"$sort": {"n": -1}},
    ]):
        k = d["_id"]
        print(f"  operacion={k.get('operacion')!s:<12} etapa={k.get('etapa')!s:<12} "
              f"n={d['n']:>6}  [{d.get('min_f')} .. {d.get('max_f')}]")

    print("\nLectura:")
    print("  - NegocioMov CON FCI pero Operaciones casi vacío → el job no las pasó (correr --full).")
    print("  - NegocioMov SIN FCI reciente → el corte es la INGESTA (negocio_movimientos/aunesa).")
    print("  - LIQ sólo con ^BOL (0 en ^CL) → no hay liquidaciones CL que pasar (normal si son FCI comunes).")
    print("  - Operaciones las tiene pero la vista no las muestra → es la vista (filtro).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
