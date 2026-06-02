"""Diag READ-ONLY: shape exacto de los FCI bilateral ya cargados en Operaciones,
para que el job que los traiga de NegocioMovimientos produzca docs IDÉNTICOS.

Contesta (REGLA #2):
  1. Cómo lucen los CL (liquidaciones) que ya están en CashFlow.Operaciones
     (dump completo de campos) → de ahí sale el mapeo del job.
  2. ¿Ya hay DOC (solicitudes) en Operaciones, o solo CL?
  3. Distribución de prefijos de `boleto` en Operaciones (BOL vs CL vs DOC vs otros).
  4. En NegocioMovimientos: por categoría FCI, prefijo de comprobante (CL/DOC) y
     cobertura de importe → confirma el mapeo etapa (CL=liquidacion / DOC=solicitud).

NO escribe nada. Cliente de solo lectura.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.diag_fci_shape
"""
from __future__ import annotations

from collections import Counter

from core.mongo import get_mongo_client_read

_FCI_CATS = ["suscripcion_fci", "rescate_fci", "solicitud_suscripcion_fci", "solicitud_rescate_fci"]


def _prefijo(s) -> str:
    s = str(s or "")
    return s.split()[0] if s and not s[0].isdigit() else ("(numérico)" if s[:1].isdigit() else "(vacío)")


def main() -> None:
    db = get_mongo_client_read()["CashFlow"]
    ops = db["Operaciones"]
    mov = db["NegocioMovimientos"]

    print("══ 1) Dump COMPLETO de CL (liquidaciones FCI) ya en Operaciones ══")
    for d in ops.find({"tipo_operacion": {"$regex": "Liquidaci.*FCI", "$options": "i"}}).limit(3):
        d.pop("_id", None)
        for k, v in d.items():
            print(f"   {k:<16} {v!r}")
        print("   " + "-" * 40)

    print("\n══ 2) ¿Hay DOC (solicitudes) ya en Operaciones? ══")
    n_doc = ops.count_documents({"boleto": {"$regex": "^DOC", "$options": "i"}})
    n_cl = ops.count_documents({"boleto": {"$regex": "^CL", "$options": "i"}})
    print(f"  boleto ~ ^DOC: {n_doc} | boleto ~ ^CL: {n_cl}")

    print("\n══ 3) Prefijos de `boleto` en Operaciones (muestra de 20k) ══")
    pref: Counter[str] = Counter()
    for d in ops.find({}, {"_id": 0, "boleto": 1}).limit(20000):
        pref[_prefijo(d.get("boleto"))] += 1
    print("  ", dict(pref.most_common()))

    print("\n══ 4) NegocioMovimientos: por categoría FCI → prefijo comprobante + monto ══")
    for cat in _FCI_CATS:
        tot = mov.count_documents({"categoria": cat})
        if not tot:
            print(f"  {cat}: (0 docs)")
            continue
        prefs: Counter[str] = Counter()
        for d in mov.find({"categoria": cat}, {"_id": 0, "comprobante": 1}).limit(5000):
            prefs[_prefijo(d.get("comprobante"))] += 1
        con_imp = mov.count_documents({"categoria": cat, "importe": {"$ne": None}})
        con_cant = mov.count_documents({"categoria": cat, "cantidad": {"$ne": None}})
        print(f"  {cat}: total={tot} | prefijos(≤5k)={dict(prefs)} | importe≠null={con_imp} | cantidad≠null={con_cant}")

    print("\n══ Ejemplo COMPLETO de un CL en NegocioMovimientos (liquidación) ══")
    d = mov.find_one({"categoria": {"$in": ["suscripcion_fci", "rescate_fci"]}})
    if d:
        d.pop("_id", None)
        for k, v in d.items():
            print(f"   {k:<16} {v!r}")

    print("\n(read-only: no se escribió nada)")


if __name__ == "__main__":
    main()
