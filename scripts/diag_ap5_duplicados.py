"""scripts/diag_ap5_duplicados.py — ¿por qué PositionReport repite la misma clave?

READ-ONLY. Un GET a `PosTrade/PositionReport`. No escribe nada.

**El problema que investiga.** Con `viewDetails=true`, la cámara devolvió 98
filas para la misma cuenta, el mismo símbolo y el mismo tipo de posición. La PK
que eligió `ap5.portfolio` —(fecha, cuenta, símbolo, tipo)— asume que eso no
pasa, así que un UPSERT se quedaría con UNA y perdería las otras 97 **sin
fallar**: la tabla existe, el job dice OK, y la posición está mal contada.

**Las dos hipótesis, que se arreglan distinto:**

  A) Hay un campo que las DISTINGUE y lo estamos descartando (un número de
     lote, un contrato, una contraparte). Entonces falta grano en la PK y la
     posición NO se suma: cada fila es una cosa distinta.

  B) Son PARTES de la misma posición y hay que AGREGARLAS. Entonces la PK está
     bien y lo que falta es sumar antes de escribir.

Elegir mal es caro en las dos direcciones: sumar lo que no se suma infla la
posición; no sumar lo que sí se suma la trunca.

**Cómo las distingue este script:** toma la clave más repetida, junta todas sus
filas crudas y compara campo por campo. Si algún campo cambia entre filas, ese
es el discriminador (hipótesis A). Si TODOS los campos son idénticos, no hay
nada que las distinga y la única lectura posible es que se sumen (hipótesis B).

También compara contra `viewDetails=false`, que según el manual devuelve el
portfolio RESUMIDO: si ese ya trae la posición consolidada, puede ser
directamente la respuesta correcta y no hace falta agregar nada a mano.

Uso:
    python -m scripts.diag_ap5_duplicados
    python -m scripts.diag_ap5_duplicados --fecha 20260821
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict

from core import postrade
from core.postrade_posicion import SECURITY_TYPE_FUTURO
from jobs.ap5_portfolio import ultimo_dia_habil

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEP = "=" * 78


def _pedir(fecha: str, detalle: bool) -> list:
    v = postrade.leer("PositionReport", {
        "clearingBusinessDate": fecha,
        "viewDetails": "true" if detalle else "false",
    })
    return v if isinstance(v, list) else []


def _futuros(crudo: list) -> list[dict]:
    out = []
    for p in crudo:
        if not isinstance(p, dict):
            continue
        inst = p.get("Instrument") or {}
        if isinstance(inst, dict) and str(inst.get("SecurityType") or "").strip() == SECURITY_TYPE_FUTURO:
            out.append(p)
    return out


def _clave(p: dict) -> tuple:
    inst = p.get("Instrument") or {}
    tipos = [q.get("PosType") for q in (p.get("PositionQty") or []) if isinstance(q, dict)]
    return (str(p.get("Account")), str(inst.get("Symbol")), "|".join(map(str, tipos)))


def _aplanar_campos(p: dict) -> dict:
    """Todos los campos del registro, incluidos los anidados, como texto plano.

    Se comparan TODOS —también los que el job hoy descarta— porque justamente
    la pregunta es si alguno de los descartados es el que distingue las filas.
    """
    plano = {}
    for k, v in p.items():
        if k == "Instrument" and isinstance(v, dict):
            for ik, iv in v.items():
                plano[f"Instrument.{ik}"] = json.dumps(iv, ensure_ascii=False)
        elif k == "PositionQty" and isinstance(v, list):
            for i, q in enumerate(v):
                if isinstance(q, dict):
                    for qk, qv in q.items():
                        plano[f"PositionQty[{i}].{qk}"] = json.dumps(qv, ensure_ascii=False)
        else:
            plano[k] = json.dumps(v, ensure_ascii=False)
    return plano


def main() -> None:
    ap = argparse.ArgumentParser(description="Investiga las claves repetidas de PositionReport.")
    ap.add_argument("--fecha", help="AAAAMMDD (default: último día hábil)")
    args = ap.parse_args()
    fecha = postrade.fecha_api(args.fecha or ultimo_dia_habil())

    print(SEP)
    print(f"PositionReport — claves repetidas al {fecha}")
    print(SEP)

    detalle = _futuros(_pedir(fecha, detalle=True))
    print(f"  viewDetails=true  → {len(detalle)} registros de futuros")

    repes = Counter(_clave(p) for p in detalle)
    peor, veces = repes.most_common(1)[0] if repes else ((None,), 0)
    total_repetidas = sum(v for v in repes.values() if v > 1)
    print(f"  claves únicas     : {len(repes)}")
    print(f"  registros en claves repetidas: {total_repetidas}")

    if veces <= 1:
        print("\n  ✓ No hay claves repetidas en esta fecha. Nada que investigar.")
        return

    print(f"\n  Clave más repetida: cuenta={peor[0]} símbolo={peor[1]} tipo={peor[2]} → {veces} filas")

    grupo = [p for p in detalle if _clave(p) == peor]

    # ── ¿Hay algún campo que las distinga? ──
    print()
    print(SEP)
    print("¿QUÉ CAMBIA ENTRE ESAS FILAS?")
    print(SEP)
    valores = defaultdict(set)
    for p in grupo:
        for k, v in _aplanar_campos(p).items():
            valores[k].add(v)

    varian = {k: v for k, v in valores.items() if len(v) > 1}
    iguales = sorted(k for k, v in valores.items() if len(v) == 1)

    if varian:
        print("  CAMPOS QUE VARÍAN (candidatos a discriminador):\n")
        for k in sorted(varian):
            muestras = sorted(varian[k])[:6]
            print(f"    {k:<34} {len(varian[k])} valores distintos: {', '.join(muestras)}")
    else:
        print("  NINGÚN campo varía: las filas son IDÉNTICAS en todos sus campos.")

    print(f"\n  campos iguales en las {veces} filas: {', '.join(iguales)}")

    # ── ¿Cuánto suman? ──
    largo = corto = 0.0
    settl = 0.0
    for p in grupo:
        for q in (p.get("PositionQty") or []):
            if isinstance(q, dict):
                largo += float(q.get("LongQty") or 0)
                corto += float(q.get("ShortQty") or 0)
        settl += float(p.get("DailySettlement") or 0)
    print(f"\n  Si se SUMARAN: long={largo:,.2f} short={corto:,.2f} settlement={settl:,.2f}")
    print(f"  Si se pisaran (lo que hace el UPSERT hoy): se guarda 1 de {veces} filas")

    # ── ¿Y el resumido qué dice? ──
    print()
    print(SEP)
    print("CONTRASTE CON viewDetails=false (el portfolio RESUMIDO)")
    print(SEP)
    resumido = _futuros(_pedir(fecha, detalle=False))
    print(f"  viewDetails=false → {len(resumido)} registros de futuros")
    repes_r = Counter(_clave(p) for p in resumido)
    reps_r = sum(v for v in repes_r.values() if v > 1)
    print(f"  claves únicas     : {len(repes_r)} | registros en claves repetidas: {reps_r}")

    coincide = [p for p in resumido if _clave(p) == peor]
    if coincide:
        print(f"\n  La MISMA clave en el resumido aparece {len(coincide)} vez/veces:")
        for p in coincide[:3]:
            qs = p.get("PositionQty") or []
            print(f"    DailySettlement={p.get('DailySettlement')} "
                  f"SettlPrice={p.get('SettlPrice')} AvgPX={p.get('AvgPX')} PositionQty={qs}")
        print("\n  → COMPARAR ese PositionQty con la suma de arriba:")
        print(f"     suma del detalle : long={largo:,.2f} short={corto:,.2f}")
        print("     Si coinciden, el resumido YA ES la posición consolidada y")
        print("     alcanza con usarlo (sin agregar nada a mano).")
    else:
        print("\n  ⚠️ Esa clave NO aparece en el resumido — no son la misma vista.")

    print()
    print(SEP)
    print("CÓMO SE LEE ESTO")
    print(SEP)
    print("  · Si arriba hay CAMPOS QUE VARÍAN → cada fila es una cosa distinta.")
    print("    Falta grano en la PK y NO hay que sumar.")
    print("  · Si NO varía ningún campo → no hay nada que las distinga, así que")
    print("    son partes de la misma posición y van AGREGADAS.")
    print("  · Si el resumido ya trae la consolidada y coincide con la suma →")
    print("    la respuesta más simple es usar viewDetails=false y listo.")


if __name__ == "__main__":
    main()
