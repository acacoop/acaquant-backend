"""scripts/diag_agro_otc.py — READ-ONLY: por qué se pierden futuros agro del volumen.

Mide cuántos boletos de FUTUROS quedan FUERA del volumen AGRO y por qué motivo,
replicando la lógica de operaciones_informes.clasificar_commodity SIN tocar nada.
Sirve para decidir (con números, no con sensación) si hay que incluir las cuentas
OTC y/o ajustar la clasificación, antes de cambiar la función + backfillear.

Motivos posibles (mutuamente excluyentes, en el mismo orden que la función):
  - financieros        : tipo_operacion tiene 'FINANCIEROS' (futuros financieros, no agro)
  - otc_instrumento    : 'OTC' en el INSTRUMENTO
  - otc_denominacion   : 'OTC' en la DENOMINACIÓN (la cuenta)
  - sin_match_grano    : instrumento no matchea SOJ/TRI/MAI (otro subyacente)
  - OK:SOJA/TRIGO/MAIZ : sí entra al volumen agro

Por cada motivo: # boletos, # cuentas distintas y Σ toneladas que se ganarían/tiene
(|cantidad| × 10 si 'MIN' en instrumento, sino × 100).

Uso:
    python -m scripts.diag_agro_otc
"""
from __future__ import annotations

from collections import defaultdict

from core.mongo import get_mongo_client_read


def _toneladas(cantidad, instrumento: str) -> float:
    q = abs(cantidad or 0)
    factor = 10 if "MIN" in (instrumento or "").upper() else 100
    return q * factor


def _motivo(tipo_operacion: str, denominacion: str, instrumento: str) -> str:
    t = (tipo_operacion or "").upper()
    if "FUTUROS" not in t:
        return "no_futuro"          # no debería entrar (filtramos por Futuros)
    if "FINANCIEROS" in t:
        return "financieros"
    inst = (instrumento or "").upper()
    if "OTC" in inst:
        return "otc_instrumento"
    if "OTC" in (denominacion or "").upper():
        return "otc_denominacion"
    if "SOJ" in inst:
        return "OK:SOJA"
    if "TRI" in inst:
        return "OK:TRIGO"
    if "MAI" in inst:
        return "OK:MAIZ"
    return "sin_match_grano"


def main() -> int:
    db = get_mongo_client_read()["CashFlow"]
    ops = db["Operaciones"]

    # Candidatos: cualquier boleto con 'Futuros' en el tipo (incluye financieros
    # y OTC — justamente lo que queremos contar). Regex one-shot (diag).
    q = {"tipo_operacion": {"$regex": "futuros", "$options": "i"}}
    proj = {"_id": 0, "tipo_operacion": 1, "instrumento": 1, "denominacion": 1,
            "cantidad": 1, "concertacion": 1, "cuenta": 1, "commodity": 1}

    boletos = defaultdict(int)
    toneladas = defaultdict(float)
    cuentas = defaultdict(set)
    # Muestras para inspección humana (qué son esas cuentas/instrumentos).
    muestra_denom = defaultdict(set)
    muestra_inst = defaultdict(set)
    # Cross-check: ¿el motivo calculado coincide con el commodity materializado?
    desync = 0
    total = 0

    for d in ops.find(q, proj):
        total += 1
        m = _motivo(d.get("tipo_operacion"), d.get("denominacion"), d.get("instrumento"))
        boletos[m] += 1
        toneladas[m] += _toneladas(d.get("cantidad"), d.get("instrumento") or "")
        if d.get("denominacion"):
            cuentas[m].add(d["denominacion"])
        if m in ("otc_denominacion", "sin_match_grano") and len(muestra_denom[m]) < 25:
            muestra_denom[m].add(d.get("denominacion") or "(sin)")
        if m in ("otc_instrumento", "sin_match_grano") and len(muestra_inst[m]) < 25:
            muestra_inst[m].add(d.get("instrumento") or "(sin)")
        # commodity materializado actual (lo que hoy ve el endpoint)
        comm = d.get("commodity")
        esperado = m[3:] if m.startswith("OK:") else None
        if comm != esperado:
            desync += 1

    print("=" * 68)
    print("DIAG agro/OTC — futuros en CashFlow.Operaciones (read-only)")
    print(f"total boletos con 'Futuros' en el tipo: {total:,}")
    print("=" * 68)
    orden = ["OK:SOJA", "OK:TRIGO", "OK:MAIZ", "otc_denominacion", "otc_instrumento",
             "sin_match_grano", "financieros", "no_futuro"]
    otros = [m for m in boletos if m not in orden]
    print(f"\n{'motivo':<18}{'boletos':>10}{'cuentas':>9}{'toneladas':>16}")
    print("-" * 53)
    for m in orden + otros:
        if m not in boletos:
            continue
        print(f"{m:<18}{boletos[m]:>10,}{len(cuentas[m]):>9,}{toneladas[m]:>16,.0f}")

    excluidas_otc = boletos["otc_denominacion"] + boletos["otc_instrumento"]
    ton_otc = toneladas["otc_denominacion"] + toneladas["otc_instrumento"]
    ctas_otc = len(cuentas["otc_denominacion"] | cuentas["otc_instrumento"])
    print("-" * 53)
    print(f"\n➤ Excluido SOLO por OTC: {excluidas_otc:,} boletos · {ctas_otc:,} cuentas "
          f"· {ton_otc:,.0f} toneladas")
    ton_ok = toneladas["OK:SOJA"] + toneladas["OK:TRIGO"] + toneladas["OK:MAIZ"]
    if ton_ok:
        print(f"  (hoy el volumen agro suma {ton_ok:,.0f} t → "
              f"incluir OTC lo subiría ~{100 * ton_otc / ton_ok:.0f}%)")

    if muestra_denom["otc_denominacion"]:
        print("\nMuestra de DENOMINACIONES con OTC (excluidas):")
        for s in sorted(muestra_denom["otc_denominacion"]):
            print(f"   {s}")
    if muestra_inst["otc_instrumento"]:
        print("\nMuestra de INSTRUMENTOS con OTC (excluidos):")
        for s in sorted(muestra_inst["otc_instrumento"]):
            print(f"   {s}")
    if muestra_denom["sin_match_grano"] or muestra_inst["sin_match_grano"]:
        print("\nMuestra 'sin_match_grano' (futuro no SOJ/TRI/MAI — ¿otro grano?):")
        for s in sorted(muestra_inst["sin_match_grano"]):
            print(f"   inst: {s}")

    print(f"\nDesync commodity materializado vs recalculado: {desync:,} boletos")
    print("(si es > 0, además del cambio de lógica hay que backfillear el campo commodity)")
    print("\nread-only: no se escribió nada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
