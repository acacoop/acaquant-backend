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

⚠️ **ACTIVO INTEGRADO no es todo el ALyC: son DOS cuentas.** Y eso NUNCA
dependió de que la API sepa filtrar: la fila de `AccountBalance` ya viene con
`ClearingAccountCode` y `AccountOwner`, así que el recorte se puede hacer
siempre de nuestro lado. Lo que sí hay que averiguar —y por eso este diag
imprime el universo COMPLETO— es (a) el GRANO de la respuesta (¿una fila por
cuenta, o una sola ya consolidada para todo el ALyC?) y (b) cuáles son los dos
códigos, que se leen de la columna `cta.compens.`.

Uso:
    python -m scripts.diag_ap5_margenes                       # el universo completo
    python -m scripts.diag_ap5_margenes --cuentas 149667,155235
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


# Las grafías con que la API podría aceptar un filtro de cuenta. Se prueban
# TODAS porque el manual no documenta ninguna para este método, y un parámetro
# que no existe puede voltear la llamada entera (pasó con 1816: el campo era
# `spread` y `margen`/`margin` daban HTTP 400).
FILTROS_CUENTA = ("clearingAccountCode", "accountCode", "account",
                  "accountingAccountCode", "nettingAccountCode")


def _filas(r: Any) -> list[dict]:
    if isinstance(r, list):
        return [x for x in r if isinstance(x, dict)]
    return [r] if isinstance(r, dict) else []


def _mostrar(filas: list[dict], titulo: str) -> None:
    """Las filas COMPLETAS, no una muestra. Son pocas y lo que hay que decidir
    es el GRANO: si hay una por cuenta, una por moneda, o una sola para todo."""
    print(f"\n    {titulo}: {len(filas)} fila(s)")
    if not filas:
        return
    print(f"      {'cta.compens.':<14} {'cta.contable':<14} {'moneda':<12} "
          f"{'balance':>18}  titular")
    for x in filas:
        print(f"      {x.get('ClearingAccountCode','')!s:<14} "
              f"{x.get('AccountingAccountCode','')!s:<14} "
              f"{x.get('Currency','')!s:<12} "
              f"{float(x.get('Balance') or 0):>18,.2f}  "
              f"{str(x.get('AccountOwner',''))[:34]}")


def _saldos(f: str, cuenta_contable: str, cuentas: list[str]) -> None:
    print("\n" + "=" * 74)
    print("2) ACTIVO INTEGRADO — PosTrade/AccountBalance")
    print("=" * 74)
    print(f"   cuenta contable {cuenta_contable} = "
          f"{CUENTAS_CONTABLES.get(cuenta_contable, '(desconocida)')}")

    base = {"date": f, "accountTypeCode": cuenta_contable}
    r = _leer("AccountBalance", base, f"cuenta contable {cuenta_contable}, TODO")
    todas = _filas(r)
    _mostrar(todas, "universo completo")

    if todas:
        # El GRANO: si hay una fila por cuenta, filtrar es quedarse con dos. Si
        # hay UNA sola para todo, el número ya viene consolidado y no hay nada
        # que filtrar — son dos mundos distintos y hay que saber cuál es.
        ctas = {str(x.get("ClearingAccountCode", "")) for x in todas}
        mon = {str(x.get("Currency", "")) for x in todas}
        print(f"\n    → cuentas distintas en la respuesta: {len(ctas)}  {sorted(ctas)}")
        print(f"    → monedas distintas: {sorted(mon)}")
        if len(todas) == 1:
            print("    ⚠️ UNA sola fila: el saldo ya viene CONSOLIDADO para el ALyC.")
            print("       Si el reporte necesita dos cuentas por separado, hay que")
            print("       pedirlas de a una (ver el sondeo de filtros de abajo).")

    # ¿Filtra el servidor? Si sí, es más barato y trae solo lo nuestro. Si no,
    # filtramos acá: la fila YA trae `ClearingAccountCode`, así que el filtro
    # nunca dependió de que la API lo soporte.
    if cuentas:
        print(f"\n    ¿La API filtra por cuenta? (probando con {cuentas[0]})")
        anduvo = None
        for clave in FILTROS_CUENTA:
            rr = _leer("AccountBalance", {**base, clave: cuentas[0]}, f"param «{clave}»")
            if rr is None:
                continue
            fl = _filas(rr)
            # Que responda no alcanza: si devuelve LO MISMO que sin filtro, el
            # parámetro se está ignorando en silencio — que es peor que un 400,
            # porque parece que anduvo.
            if len(fl) < len(todas):
                print(f"      ⇒ «{clave}» FILTRA de verdad ({len(todas)} → {len(fl)})")
                anduvo = clave
                _mostrar(fl, f"con {clave}={cuentas[0]}")
                break
            print(f"      ~ «{clave}» respondió pero devolvió lo mismo: lo IGNORA")
        if not anduvo:
            print("      ⇒ ninguno filtra: el filtro va de NUESTRO lado, por")
            print("        `ClearingAccountCode`. La fila ya lo trae, así que alcanza.")

        # Y el número que iría en la card, filtrando acá.
        elegidas = [x for x in todas
                    if str(x.get("ClearingAccountCode", "")) in set(cuentas)]
        _mostrar(elegidas, f"filtrado NUESTRO por {cuentas}")
        if elegidas:
            print("\n    ACTIVO INTEGRADO (por moneda, sin sumar entre monedas):")
            por: dict[str, float] = {}
            for x in elegidas:
                m = str(x.get("Currency", "") or "(sin moneda)")
                por[m] = por.get(m, 0.0) + float(x.get("Balance") or 0)
            for m, v in sorted(por.items()):
                print(f"      {m:<14} {v:>18,.2f}")
        else:
            print(f"\n    ⚠️ Ninguna de {cuentas} aparece en la respuesta.")
            print("       Puede ser que el código sea otro (mirá la columna cta.compens.)")
            print("       o que hoy no tengan saldo en esta cuenta contable.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Márgenes y activo integrado (read-only).")
    ap.add_argument("--fecha", help="AAAAMMDD (default: HOY)")
    ap.add_argument("--cuenta-contable", default=CUENTA_GARANTIA_INICIAL,
                    help=f"default {CUENTA_GARANTIA_INICIAL} (Gtía inicial)")
    ap.add_argument("--cuentas", default="",
                    help="las cuentas del ACTIVO INTEGRADO, separadas por coma "
                         "(ej. 149667,155235). Sin esto muestra el universo completo.")
    args = ap.parse_args()

    f = postrade.fecha_api(args.fecha) if args.fecha else date.today().strftime("%Y%m%d")
    print("=" * 74)
    print("AP5 · las dos cosas que faltan en la cabecera del reporte")
    print(f"fecha: {f}   (HOY — márgenes y garantías son el estado de hoy,")
    print("             a diferencia de la posición, que es de cierre)")
    print("=" * 74)
    print("\nREAD-ONLY. Un ✗ NO es un problema: es el dato que vinimos a buscar.")

    cuentas = [c.strip() for c in args.cuentas.split(',') if c.strip()]
    _margenes(f)
    _saldos(f, args.cuenta_contable, cuentas)

    print("\n" + "=" * 74)
    print("Qué mirar:")
    print("  · Qué grafía del parámetro anduvo (queda escrita en el job).")
    print("  · El GRANO de AccountBalance: ¿una fila para todo, o una por cuenta?")
    print("  · La MONEDA de cada importe — no se suman entre sí.")
    print("  · Si el total con y sin `viewDetails` coincide.")
    print("\nCon eso se decide qué persistir y se arma el job, igual que ap5_portfolio.")


if __name__ == "__main__":
    main()
