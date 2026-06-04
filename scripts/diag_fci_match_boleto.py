"""scripts/diag_fci_match_boleto.py — verifica el MATCH por boleto antes de codear
el fix del bruto FCI (REGLA #2).

Plan elegido: cuando el API trae la suscripción FCI con bruto=0, el job le pisa el
bruto correcto desde NegocioMovimientos, matcheando por boleto. Para que eso sea
seguro hay que confirmar:
  1. El `boleto` de Operaciones == `comprobante` (BOL) de NegocioMov (1:1).
  2. En Operaciones el bruto está realmente en 0 (suscripción) y bien (rescate).
  3. El importe de NegocioMov es el valor correcto a estampar.

Toma los comprobantes FCI (suscripcion_fci / rescate_fci) de NegocioMov del día y
los busca en Operaciones por boleto, mostrando el desajuste fila por fila.

Read-only. Default HOY. Correr en el Droplet:
    python -m scripts.diag_fci_match_boleto
    python -m scripts.diag_fci_match_boleto --fecha 2026-06-04
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read

_CATS = ("suscripcion_fci", "rescate_fci")


def run(fecha: str) -> dict:
    db = get_mongo_client_read()["CashFlow"]
    mov, ops = db["NegocioMovimientos"], db["Operaciones"]

    # FCI normal del día en NegocioMov (los BOL — los que entran por el API).
    neg = list(mov.find(
        {"categoria": {"$in": list(_CATS)}, "fecha": fecha},
        {"_id": 0, "comprobante": 1, "categoria": 1, "importe": 1},
    ))
    if not neg:
        print(f"Sin FCI (suscripcion/rescate) en NegocioMov para {fecha}.")
        return {"neg": 0}

    boletos = [str(d["comprobante"]).strip() for d in neg if d.get("comprobante")]
    ops_por_boleto = {
        str(d["boleto"]).strip(): d
        for d in ops.find(
            {"boleto": {"$in": boletos}},
            {"_id": 0, "boleto": 1, "bruto": 1, "operacion": 1, "tipo_operacion": 1, "mercado": 1},
        )
    }

    print(f"Día {fecha} · {len(neg)} movimientos FCI en NegocioMov\n")
    print(f"  {'comprobante':16} {'categoria':16} {'neg_importe':>16} "
          f"{'en Ops?':8} {'ops_bruto':>16} {'ops_operacion':14}")
    n_match = n_falta = n_bruto0 = n_bruto_ok = n_bruto_dif = 0
    for d in neg:
        comp = str(d.get("comprobante") or "").strip()
        cat = d.get("categoria") or "?"
        imp = abs(d.get("importe") or 0)
        o = ops_por_boleto.get(comp)
        if o is None:
            n_falta += 1
            print(f"  {comp:16} {cat:16} {imp:>16,.0f} {'NO':8} {'—':>16} {'—':14}")
            continue
        n_match += 1
        ob = o.get("bruto")
        obv = ob if isinstance(ob, (int, float)) else None
        if obv in (None, 0):
            n_bruto0 += 1
        elif abs((obv or 0) - imp) < 1:
            n_bruto_ok += 1
        else:
            n_bruto_dif += 1
        print(f"  {comp:16} {cat:16} {imp:>16,.0f} {'sí':8} "
              f"{(obv if obv is not None else 0):>16,.0f} {(o.get('operacion') or '?'):14}")

    print("\n── Resumen ──")
    print(f"  matchean por boleto      : {n_match}/{len(neg)}")
    print(f"  faltan en Operaciones    : {n_falta}")
    print(f"  con bruto=0/None (ROTO)  : {n_bruto0}  ← los que el fix corregiría")
    print(f"  con bruto correcto       : {n_bruto_ok}  ← NO tocar")
    print(f"  con bruto DISTINTO       : {n_bruto_dif}  ← revisar a mano si hay")
    if n_match == len(neg) and n_bruto_dif == 0:
        print("\n  ✓ Match 1:1 limpio. El fix por boleto es seguro: $set bruto solo donde está 0.")
    else:
        print("\n  ⚠ Hay faltantes o diferencias — revisar antes de codear el fix.")
    return {"neg": len(neg), "match": n_match, "falta": n_falta,
            "bruto0": n_bruto0, "bruto_ok": n_bruto_ok, "bruto_dif": n_bruto_dif}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha", help="día exacto YYYY-MM-DD (default HOY ART)")
    args = ap.parse_args()
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()
    res = run(args.fecha or hoy.isoformat())
    print(f"\n→ {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
