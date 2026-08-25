"""scripts/diag_ap5_margenes.py — probar MÁRGENES y ACTIVO INTEGRADO.

READ-ONLY. Solo GET (`postrade.leer`), nunca escritura.

Las dos cosas que le faltan a la cabecera del reporte de la mesa:

    Requerimiento de Márgenes  →  PosTrade/MarginRequirementReport
    Activo Integrado           →  PosTrade/AccountBalance, cuenta contable 12

**La fecha es HOY**, no el último día hábil. Es lo que pidió el user y tiene
sentido: a diferencia de la posición —que es de CIERRE (`SettlSessID = EOD`) y
por eso se pide del día anterior— un requerimiento de márgenes y una garantía
integrada son el estado de HOY.

⚠️ **El nombre del parámetro no se adivina.** El manual escribe `Date` en la
tabla y `date=` en el ejemplo de URL. Un parámetro mal escrito puede hacer que
la API rechace la llamada entera (ya pasó con 1816: `margen`/`margin` daban HTTP
400 y el campo era `spread`). Así que se prueban las dos grafías y se reporta
cuál anduvo — el resultado queda escrito acá y no hay que volver a averiguarlo.

⚠️ **`viewDetails` NO es "lo mismo con más detalle".** Con `true` la MISMA
cuenta aparece una vez por grupo de producto (DLR, SOJ…). Sumar las dos
respuestas juntas contaría todo dos veces. Se prueban las dos por separado y se
comparan los totales: si dan distinto, esa es la razón.

Throttle: 1 petición/segundo (lo aplica `core/postrade` entre procesos), así que
el sondeo tarda unos segundos.

Uso:
    python -m scripts.diag_ap5_margenes
    python -m scripts.diag_ap5_margenes --fecha 20260825
    python -m scripts.diag_ap5_margenes --cuenta-contable 14
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any

from core import postrade
from core.postrade_margenes import (
    CUENTA_GARANTIA_INICIAL,
    CUENTAS_CONTABLES,
    aplanar_margenes,
    totales_por_moneda,
)


def _forma(v: Any) -> str:
    """La FORMA de la respuesta, sin volcarla entera: lo que hace falta para
    decidir es el grano y los nombres de los campos, no 400 filas."""
    if isinstance(v, list):
        return "lista VACÍA" if not v else f"lista de {len(v)} → {_forma(v[0])}"
    if isinstance(v, dict):
        if not v:
            return "dict vacío"
        ks = list(v)[:16]
        return "{" + ", ".join(ks) + ("" if len(v) <= 16 else f" … +{len(v)-16}") + "}"
    return type(v).__name__


def _leer(nombre: str, params: dict, etiqueta: str) -> Any | None:
    print(f"\n  → {etiqueta}")
    print(f"    params: {params}")
    try:
        r = postrade.leer(nombre, params)
    except Exception as e:  # el objetivo ES ver qué falla: un ✗ acá es el dato
        print(f"    ✗ {type(e).__name__}: {str(e)[:200]}")
        return None
    print(f"    ✓ {_forma(r)}")
    return r


def _margenes(f: str) -> None:
    print("\n" + "=" * 74)
    print("1) REQUERIMIENTO DE MÁRGENES — PosTrade/MarginRequirementReport")
    print("=" * 74)

    # Las dos grafías del parámetro. El manual usa las dos y no son
    # necesariamente equivalentes del lado del servidor.
    crudo = None
    for clave in ("date", "Date"):
        crudo = _leer("MarginRequirementReport", {clave: f},
                      f"sin desglose, parámetro «{clave}»")
        if crudo:
            print(f"    ⇒ la grafía que anda es «{clave}»")
            break

    if not crudo:
        print("\n  No respondió con datos. Puede ser que hoy no haya márgenes, o")
        print("  que el método no esté habilitado para nuestro usuario.")
        return

    filas, stats = aplanar_margenes(crudo)
    print(f"\n    aplanado: {len(filas)} filas")
    print(f"    niveles : {stats}")
    if filas:
        print("\n    muestra de una fila:")
        print("      " + json.dumps(filas[0], ensure_ascii=False, default=str)[:500])
        print("\n    TOTALES POR MONEDA  ← el número de la card:")
        for t in totales_por_moneda(filas):
            print(f"      {t['moneda']:<12} margen={t['margen']:>18,.2f}  "
                  f"primas={t['primas']:>14,.2f}  inter={t['inter_temporal']:>12,.2f}  "
                  f"({t['cuentas']} cuentas)")

    # Con desglose: sirve para ver si el total cambia (y por lo tanto si las dos
    # respuestas se pueden mezclar o no — no se pueden).
    det = _leer("MarginRequirementReport", {"date": f, "viewDetails": "true"},
                "CON desglose por grupo de producto (viewDetails=true)")
    if det:
        fd, _ = aplanar_margenes(det)
        grupos = sorted({x["grupo_producto"] for x in fd if x["grupo_producto"]})
        print(f"    aplanado: {len(fd)} filas · grupos: {grupos or '(ninguno)'}")
        for t in totales_por_moneda(fd):
            print(f"      {t['moneda']:<12} margen={t['margen']:>18,.2f}")
        print("    ⚠️ Si este total NO coincide con el de arriba, las dos respuestas")
        print("       NO se pueden sumar juntas: la misma cuenta viene repetida por grupo.")


def _saldos(f: str, cuenta_contable: str) -> None:
    print("\n" + "=" * 74)
    print("2) ACTIVO INTEGRADO — PosTrade/AccountBalance")
    print("=" * 74)
    print(f"   cuenta contable {cuenta_contable} = "
          f"{CUENTAS_CONTABLES.get(cuenta_contable, '(desconocida)')}")

    for clave in ("date", "Date"):
        r = _leer("AccountBalance", {clave: f, "accountTypeCode": cuenta_contable},
                  f"cuenta {cuenta_contable}, parámetro «{clave}»")
        if r:
            print(f"    ⇒ la grafía que anda es «{clave}»")
            muestra = r[0] if isinstance(r, list) and r else r
            if isinstance(muestra, dict):
                print("\n    muestra:")
                print("      " + json.dumps(muestra, ensure_ascii=False, default=str)[:700])
                nums = [f"{k}={v}" for k, v in muestra.items()
                        if isinstance(v, (int, float)) and not isinstance(v, bool)]
                if nums:
                    print(f"    números: {', '.join(nums[:12])}")
            break
    else:
        # Sin la cuenta: si el universo completo SÍ responde, el problema es el
        # filtro y no el método — distinguirlo ahorra media hora.
        _leer("AccountBalance", {"date": f}, "SIN filtrar por cuenta contable")


def main() -> None:
    ap = argparse.ArgumentParser(description="Márgenes y activo integrado (read-only).")
    ap.add_argument("--fecha", help="AAAAMMDD (default: HOY)")
    ap.add_argument("--cuenta-contable", default=CUENTA_GARANTIA_INICIAL,
                    help=f"default {CUENTA_GARANTIA_INICIAL} (Gtía inicial)")
    args = ap.parse_args()

    f = postrade.fecha_api(args.fecha) if args.fecha else date.today().strftime("%Y%m%d")
    print("=" * 74)
    print("AP5 · las dos cosas que faltan en la cabecera del reporte")
    print(f"fecha: {f}   (HOY — márgenes y garantías son el estado de hoy,")
    print("             a diferencia de la posición, que es de cierre)")
    print("=" * 74)
    print("\nREAD-ONLY. Un ✗ NO es un problema: es el dato que vinimos a buscar.")

    _margenes(f)
    _saldos(f, args.cuenta_contable)

    print("\n" + "=" * 74)
    print("Qué mirar:")
    print("  · Qué grafía del parámetro anduvo (queda escrita en el job).")
    print("  · El GRANO de AccountBalance: ¿una fila para todo, o una por cuenta?")
    print("  · La MONEDA de cada importe — no se suman entre sí.")
    print("  · Si el total con y sin `viewDetails` coincide.")
    print("\nCon eso se decide qué persistir y se arma el job, igual que ap5_portfolio.")


if __name__ == "__main__":
    main()
