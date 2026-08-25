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
    python -m scripts.diag_ap5_margenes --cuentas 149667,218115 --barrer
    python -m scripts.diag_ap5_margenes --solo-barrido --cuentas 149667,218115
    python -m scripts.diag_ap5_margenes --cuenta-contable 14
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any

from core import postrade
from core.calendario import restar_habiles
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


def _margenes(f: str, habil: str) -> None:
    print("\n" + "=" * 74)
    print("1) REQUERIMIENTO DE MÁRGENES — PosTrade/MarginRequirementReport")
    print("=" * 74)

    # ⚠️ Una lista VACÍA **no es un error**: el método contestó 200 y no tiene
    # nada para esa fecha. Un método DESHABILITADO tira excepción. Desde la card
    # los dos se ven igual (un número que no está) y son cosas muy distintas:
    # uno se espera, el otro se pide. Por eso, si HOY viene vacío, se reintenta
    # con el ÚLTIMO DÍA HÁBIL — si ahí hay datos, el método anda y lo que pasa
    # es que los márgenes de hoy todavía no se calcularon.
    crudo = None
    cual = ""
    for etiqueta, fecha in ((f"HOY {f}", f), (f"último hábil {habil}", habil)):
        if fecha == f and etiqueta.startswith("último"):
            continue
        for clave in ("date", "Date"):
            r = _leer("MarginRequirementReport", {clave: fecha},
                      f"{etiqueta}, parámetro «{clave}»")
            if r is None:
                print(f"    ⇒ «{clave}» TIRÓ ERROR (no es que no haya datos)")
                continue
            if not r:
                print(f"    ⇒ «{clave}» contestó bien pero VACÍO para {fecha}")
                continue
            print(f"    ⇒ anduvo: grafía «{clave}», fecha {fecha}")
            crudo, cual = r, fecha
            break
        if crudo:
            break

    if not crudo:
        print("\n  Ni hoy ni el último día hábil devuelven márgenes.")
        print("  Ojo con la conclusión: contestó VACÍO, no dio error. O sea que")
        print("  el método ESTÁ habilitado — lo que no hay es dato. Puede ser")
        print("  que los márgenes se publiquen más tarde en el día, o que la")
        print("  cuenta que los tiene sea otra. Es una pregunta para la cámara,")
        print("  no un bug nuestro.")
        return

    filas, stats = aplanar_margenes(crudo)
    print(f"\n    fecha con datos: {cual}")
    print(f"    aplanado: {len(filas)} filas")
    print(f"    niveles : {stats}")
    if filas:
        print("\n    muestra de una fila:")
        print("      " + json.dumps(filas[0], ensure_ascii=False, default=str)[:500])
        print("\n    TOTALES POR MONEDA  ← el número de la card:")
        for t in totales_por_moneda(filas):
            print(f"      {t['moneda']:<12} margen={t['margen']:>18,.2f}  "
                  f"primas={t['primas']:>14,.2f}  inter={t['inter_temporal']:>12,.2f}  "
                  f"({t['cuentas']} cuentas)")

    det = _leer("MarginRequirementReport", {"date": cual, "viewDetails": "true"},
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
        ctas = {str(x.get("ClearingAccountCode", "")) for x in todas}
        mon = {str(x.get("Currency", "")) for x in todas}
        print(f"\n    → cuentas distintas en la respuesta: {len(ctas)}  {sorted(ctas)}")
        print(f"    → monedas distintas: {sorted(mon)}")
        if len(todas) == 1:
            print("    ⚠️ UNA sola fila: el saldo ya viene CONSOLIDADO para el ALyC.")

    if cuentas:
        print(f"\n    ¿La API filtra por cuenta? (probando con {cuentas[0]})")
        anduvo = None
        for clave in FILTROS_CUENTA:
            rr = _leer("AccountBalance", {**base, clave: cuentas[0]}, f"param «{clave}»")
            if rr is None:
                continue
            fl = _filas(rr)
            if len(fl) < len(todas):
                print(f"      ⇒ «{clave}» FILTRA de verdad ({len(todas)} → {len(fl)})")
                anduvo = clave
                _mostrar(fl, f"con {clave}={cuentas[0]}")
                break
            print(f"      ~ «{clave}» respondió pero devolvió lo mismo: lo IGNORA")
        if not anduvo:
            print("      ⇒ ninguno filtra: el filtro va de NUESTRO lado, por")
            print("        `ClearingAccountCode`. La fila ya lo trae, así que alcanza.")

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
            print(f"\n    ⚠️ Ninguna de {cuentas} aparece en esta cuenta contable.")
            print("       Antes de concluir que no existen: probá --barrer, que")
            print("       recorre LAS 29 cuentas contables y dice en cuál está cada")
            print("       código. Una cuenta que no está en la 12 puede estar en la")
            print("       13 o la 14 (las otras dos de integración).")


def _barrer(f: str, cuentas: list[str]) -> None:
    """Las 29 cuentas contables, de a una, para saber DÓNDE vive cada cuenta.

    Son 29 llamadas y el throttle es 1/seg, así que tarda ~30 s. Vale la pena:
    es la única forma de contestar «¿en qué cuenta contable está 149667?» sin
    adivinar, y el resultado se escribe una vez y no se vuelve a preguntar.

    ⚠️ Lo que se busca acá NO es un total. Sumar cuentas contables distintas
    entre sí no significa nada — la 12 es garantía inicial y la 22 son
    diferencias. El barrido es un MAPA, no un balance.
    """
    print("\n" + "=" * 74)
    print("3) BARRIDO — las 29 cuentas contables, ¿dónde vive cada cuenta?")
    print("=" * 74)
    print(f"   {'cód':<5} {'nombre':<30} {'filas':>6}  cuentas / monedas")

    donde: dict[str, list[str]] = {}
    for cod, nombre in CUENTAS_CONTABLES.items():
        try:
            r = postrade.leer("AccountBalance", {"date": f, "accountTypeCode": cod})
        except Exception as e:
            print(f"   {cod:<5} {nombre[:30]:<30} {'✗':>6}  {type(e).__name__}")
            continue
        filas = _filas(r)
        ctas = sorted({str(x.get("ClearingAccountCode", "")) for x in filas if x})
        mon = sorted({str(x.get("Currency", "")) for x in filas if x})
        for c in ctas:
            donde.setdefault(c, []).append(cod)
        detalle = f"{','.join(ctas)}  [{','.join(mon)}]" if filas else ""
        print(f"   {cod:<5} {nombre[:30]:<30} {len(filas):>6}  {detalle}")

    print(f"\n   → cuentas de compensación vistas en TODO el barrido: {len(donde)}")
    for c, cods in sorted(donde.items()):
        marca = "  ← la buscabas" if c in set(cuentas) else ""
        print(f"      {c:<12} en cuentas contables {','.join(cods)}{marca}")

    faltan = [c for c in cuentas if c not in donde]
    if faltan:
        print(f"\n   ⚠️ {faltan} NO aparece en NINGUNA de las 29 cuentas contables.")
        print("      Eso ya no es «no supimos filtrar»: `AccountBalance` trabaja a")
        print("      nivel CUENTA DE COMPENSACIÓN del ALyC, y esos códigos parecen")
        print("      ser de COMITENTE (los de `ap5.cuentas`). Son dos numeraciones")
        print("      distintas y no se cruzan — hay que preguntarle a la cámara")
        print("      cuál es el método que abre el integrado por comitente.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Márgenes y activo integrado (read-only).")
    ap.add_argument("--fecha", help="AAAAMMDD (default: HOY)")
    ap.add_argument("--cuenta-contable", default=CUENTA_GARANTIA_INICIAL,
                    help=f"default {CUENTA_GARANTIA_INICIAL} (Gtía inicial)")
    ap.add_argument("--cuentas", default="",
                    help="las cuentas del ACTIVO INTEGRADO, separadas por coma "
                         "(ej. 149667,218115).")
    ap.add_argument("--barrer", action="store_true",
                    help="recorre LAS 29 cuentas contables y dice en cuál está "
                         "cada cuenta de compensación (~30 s por el throttle).")
    ap.add_argument("--solo-barrido", action="store_true",
                    help="saltea márgenes y el bloque 2: solo el mapa.")
    args = ap.parse_args()

    hoy = date.today()
    f = postrade.fecha_api(args.fecha) if args.fecha else hoy.strftime("%Y%m%d")
    habil = restar_habiles(hoy, 1).strftime("%Y%m%d")

    print("=" * 74)
    print("AP5 · las dos cosas que faltan en la cabecera del reporte")
    print(f"fecha: {f}   (último día hábil de referencia: {habil})")
    print("=" * 74)
    print("\nREAD-ONLY. Un ✗ NO es un problema: es el dato que vinimos a buscar.")

    cuentas = [c.strip() for c in args.cuentas.split(',') if c.strip()]
    if not args.solo_barrido:
        _margenes(f, habil)
        _saldos(f, args.cuenta_contable, cuentas)
    if args.barrer or args.solo_barrido:
        _barrer(f, cuentas)

    print("\n" + "=" * 74)
    print("Qué mirar:")
    print("  · VACÍO ≠ ERROR. Vacío es «el método anda y no hay dato».")
    print("  · El GRANO de AccountBalance: la fila es del ALyC, no del comitente.")
    print("  · La MONEDA de cada importe — no se suman entre sí.")
    print("  · Cuentas contables distintas TAMPOCO se suman entre sí.")


if __name__ == "__main__":
    main()
