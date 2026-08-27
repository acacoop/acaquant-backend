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
    python -m scripts.diag_ap5_margenes --crudo 218115,149667
    python -m scripts.diag_ap5_margenes --cuentas 149667,218115 --barrer
    python -m scripts.diag_ap5_margenes --solo-barrido --cuentas 149667,218115
    python -m scripts.diag_ap5_margenes --cuenta-contable 14
"""
from __future__ import annotations

import argparse
import json
import sys
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


def _margenes(f: str, habil: str, cuentas: list[str]) -> None:
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

    # ── EL DESGLOSE POR CUENTA DE NETEO ───────────────────────────────────
    # Es LA razón por la que este método importa: `AccountBalance` da un
    # agregado (un Balance por cuenta de compensación) y se probó que NO se
    # puede abrir. Acá el desglose viene de fábrica, en el tercer nivel.
    if filas:
        print("\n    ── POR CUENTA DE NETEO (el comitente) " + "─" * 30)
        por: dict[tuple[str, str], dict] = {}
        for x in filas:
            k = (str(x["cuenta"]), str(x["moneda"]))
            d = por.setdefault(k, {"margen": 0.0, "nombre": x.get("cuenta_nombre") or "",
                                   "comp": x.get("cuenta_compensacion_codigo") or "",
                                   "n": 0})
            d["margen"] += float(x.get("margen") or 0)
            d["n"] += 1
        pedidas = set(cuentas)
        print(f"    {'cuenta':<12} {'comp.':<10} {'moneda':<10} {'margen':>18} "
              f"{'refs':>5}  titular")
        for (cta, mon), d in sorted(por.items()):
            marca = "  ← LA QUE PEDISTE" if cta in pedidas else ""
            print(f"    {cta:<12} {d['comp']:<10} {mon:<10} {d['margen']:>18,.2f} "
                  f"{d['n']:>5}  {d['nombre'][:26]}{marca}")
        print(f"\n    ⇒ {len({c for c, _ in por})} cuentas de neteo distintas")
        if pedidas:
            hay = {c for c, _ in por} & pedidas
            print(f"    ⇒ de las que pediste {sorted(pedidas)}: "
                  f"{sorted(hay) if hay else 'NINGUNA aparece'}")

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


def _campos_que_coinciden(fila: dict, aguja: str) -> tuple[list[str], bool]:
    """Qué claves de la fila traen la aguja, y si alguna es IGUAL (no contiene).

    La diferencia importa: `'1172' in '211725'` es verdadero y no significa
    nada. Un match por igualdad es identidad; uno por substring es una pista que
    hay que mirar con los ojos. Se devuelven las dos cosas para poder avisar.
    """
    donde, exacto = [], False
    for k, v in fila.items():
        if v is None:
            continue
        t = str(v)
        if aguja in t:
            donde.append(k)
            if t.strip() == aguja:
                exacto = True
    return donde, exacto


def _crudo(f: str, cuentas: list[str], contables: list[str]) -> None:
    """El JSON **tal cual lo devuelve la API**, para UNA O VARIAS cuentas.

    ⚠️ **No se filtra por `ClearingAccountCode`.** Medido el 2026-08-25: las dos
    cuentas del reporte se identifican por campos DISTINTOS — `218115` es un
    `ClearingAccountCode` y `149667` aparece en `AccountOwner` /
    `AccountingAccountCode`. Un filtro por la clave que suponemos encuentra una
    y pierde la otra **sin fallar**: devuelve filas, se ven bien, y falta media
    cabecera. Es REGLA #9(A) — la identidad no es el nombre del campo.

    Por eso se busca en TODOS los campos y se IMPRIME en cuál coincidió, para
    que el match sea auditable y no un acto de fe.
    """
    print("\n" + "=" * 74)
    print(f"4) RESPONSE CRUDO — cuentas {cuentas}")
    print("=" * 74)

    # (cuenta, contable, moneda) → total. Es lo que termina en las cards.
    resumen: dict[tuple[str, str, str], float] = {}
    sospechosas: list[str] = []

    for cod in contables:
        nombre = CUENTAS_CONTABLES.get(cod, "(desconocida)")
        print("\n" + "=" * 74)
        print(f"  CUENTA CONTABLE {cod} — {nombre}")
        print("=" * 74)
        try:
            r = postrade.leer("AccountBalance", {"date": f, "accountTypeCode": cod})
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {str(e)[:200]}")
            continue

        todas = _filas(r)
        print(f"  {len(todas)} filas en total en esta contable")
        fallo: set[str] = set()

        for cuenta in cuentas:
            mias = []
            for x in todas:
                donde, exacto = _campos_que_coinciden(x, cuenta)
                if donde:
                    mias.append((x, donde, exacto))

            print(f"\n  ── cuenta {cuenta}: {len(mias)} fila(s) " + "─" * 40)
            if not mias:
                print(f"     (no aparece en la contable {cod})")
                fallo.add(cuenta)
                continue

            campos = sorted({k for _, d, _ in mias for k in d})
            print(f"     coincide por: {', '.join(campos)}")
            if not any(e for _, _, e in mias):
                aviso = (f"cuenta {cuenta} en contable {cod}: NINGÚN campo es "
                         f"IGUAL a «{cuenta}», solo lo contienen")
                sospechosas.append(aviso)
                print(f"     ⚠️ {aviso} — miralo con los ojos antes de usarlo")

            for i, (x, _, _) in enumerate(mias, 1):
                print(f"\n     ── fila {i}/{len(mias)} " + "─" * 42)
                print("     " + json.dumps(x, ensure_ascii=False, indent=2,
                                           default=str).replace("\n", "\n     "))
                m = str(x.get("Currency", "") or "(sin moneda)")
                k = (cuenta, cod, m)
                resumen[k] = resumen.get(k, 0.0) + float(x.get("Balance") or 0)

        # ⚠️ **«No aparece» sin decir QUÉ hay es un callejón sin salida.** Deja
        # al que mira sin manera de saber si el código está mal, si la contable
        # es otra, o si la respuesta vino vacía — tres cosas con el mismo
        # síntoma. Así que cuando falta una cuenta se vuelca el universo ENTERO
        # de esa contable, que es chico y contesta las tres de una.
        if fallo and todas:
            print(f"\n  ⚠️ falta(n) {sorted(fallo)} — esto es TODO lo que trae "
                  f"la contable {cod}:")
            print(f"     {'cta.compens.':<14} {'cta.contable':<14} {'moneda':<10} "
                  f"{'balance':>18}  titular")
            for x in todas:
                print(f"     {x.get('ClearingAccountCode','')!s:<14} "
                      f"{x.get('AccountingAccountCode','')!s:<14} "
                      f"{x.get('Currency','')!s:<10} "
                      f"{float(x.get('Balance') or 0):>18,.2f}  "
                      f"{str(x.get('AccountOwner',''))[:34]}")

    # ── el cuadro que alimenta la cabecera ────────────────────────────────
    print("\n" + "=" * 74)
    print("RESUMEN — lo que iría en las cards")
    print("=" * 74)
    print("  ⚠️ NO se suma entre cuentas contables: la 21 son márgenes y la 22")
    print("     diferencias. Tampoco entre monedas. Cada celda es un número.\n")
    print(f"  {'cuenta':<10} {'cont.':<6} {'concepto':<30} {'moneda':<10} {'saldo':>18}")
    for (cuenta, cod, mon), v in sorted(resumen.items()):
        print(f"  {cuenta:<10} {cod:<6} {CUENTAS_CONTABLES.get(cod,'')[:30]:<30} "
              f"{mon:<10} {v:>18,.2f}")
    if not resumen:
        print("  (ninguna de las cuentas apareció en las contables pedidas)")
    if sospechosas:
        print("\n  ⚠️ MATCHES SOLO POR SUBSTRING (revisar):")
        for a in sospechosas:
            print(f"     · {a}")


def _buscar(f: str, agujas: list[str], contables: list[str]) -> None:
    """Buscar un código en **TODOS los campos** de todas las filas.

    ⚠️ **A propósito no se busca solo en `AccountOwner`.** Es REGLA #9 otra vez:
    la identidad no está donde uno supone. Si buscamos en el campo que creemos y
    no aparece, la conclusión «no está» es falsa cuando el dato vivía en otra
    clave — y no falla nada, simplemente da vacío con seguridad. Buscar en todas
    las claves cuesta lo mismo (las filas ya vinieron) y no puede equivocarse de
    campo.

    Al final imprime **el universo entero de `AccountOwner` y de
    `ClearingAccountCode`**, que es lo que contesta la pregunta de fondo: si el
    código no está en ninguna parte, al menos se ve QUÉ hay, y de ahí se decide
    si esto es la fuente equivocada.
    """
    print("\n" + "=" * 74)
    print(f"5) BÚSQUEDA de {agujas} en TODOS los campos")
    print("=" * 74)
    print(f"   recorriendo {len(contables)} cuenta(s) contable(s)…")

    hits = 0
    duenos: dict[str, set[str]] = {}
    codigos: set[str] = set()
    for cod in contables:
        nombre = CUENTAS_CONTABLES.get(cod, "(desconocida)")
        try:
            r = postrade.leer("AccountBalance", {"date": f, "accountTypeCode": cod})
        except Exception as e:
            print(f"   {cod:<5} ✗ {type(e).__name__}")
            continue
        for x in _filas(r):
            cac = str(x.get("ClearingAccountCode", "") or "")
            own = str(x.get("AccountOwner", "") or "")
            codigos.add(cac)
            if own:
                duenos.setdefault(own, set()).add(cac)
            # todas las claves, no la que suponemos
            donde = [k for k, v in x.items()
                     if v is not None and any(a in str(v) for a in agujas)]
            if not donde:
                continue
            hits += 1
            print(f"\n   ✔ contable {cod} ({nombre}) · coincide en: {', '.join(donde)}")
            print("   " + json.dumps(x, ensure_ascii=False, indent=2,
                                     default=str).replace("\n", "\n   "))

    print("\n" + "-" * 74)
    print(f"   {hits} fila(s) con {agujas} en algún campo")
    print(f"\n   ClearingAccountCode existentes ({len(codigos)}):")
    for c in sorted(codigos):
        print(f"      {c}")
    print(f"\n   AccountOwner existentes ({len(duenos)}) — con su cuenta:")
    for o, cs in sorted(duenos.items()):
        print(f"      {o[:44]:<44} {','.join(sorted(cs))}")
    if not hits:
        print(f"\n   ⚠️ {agujas} NO aparece en NINGÚN campo de NINGUNA fila.")
        print("      Mirá la lista de arriba: si los AccountOwner son NOMBRES y no")
        print("      códigos, este método trabaja por cuenta de compensación y la")
        print("      segunda cuenta que buscás es de otra numeración — no está acá.")


# Grafías con que la API PODRÍA abrir el detalle de una cuenta. Ninguna está
# documentada para `AccountBalance` — pero `MarginRequirementReport` sí tiene
# `viewDetails`, así que la familia de métodos conoce el concepto. Se prueban de
# a una: un parámetro inexistente puede voltear la llamada entera (1816, otra
# vez: `margen`/`margin` daban HTTP 400 y el campo era `spread`).
EXPANSIONES = ("viewDetails", "details", "detail", "viewDetail", "expand",
               "includeDetails", "showDetails", "includeSubAccounts",
               "viewSubAccounts", "breakdown")


def _claves(v, prefijo: str = "") -> set[str]:
    """Las claves de TODA la estructura, incluidas las anidadas.

    Una lista de sub-cuentas adentro de la fila no se ve mirando `x.keys()`: se
    ve como una clave más, y lo que hay adentro queda invisible. Se recorre en
    profundidad para que «qué trae» sea una respuesta completa y no la primera
    capa.
    """
    out: set[str] = set()
    if isinstance(v, dict):
        for k, w in v.items():
            p = f"{prefijo}.{k}" if prefijo else str(k)
            out.add(p)
            out |= _claves(w, p)
    elif isinstance(v, list):
        for w in v[:5]:
            out |= _claves(w, f"{prefijo}[]")
    return out


def _adentro(f: str, cuenta: str, cod: str) -> None:
    """TODO lo que devuelve una cuenta en una contable, y si se puede abrir.

    Dos preguntas distintas, y la segunda es la que importa acá:

    1. **Qué trae la fila** — el JSON entero, con las claves anidadas listadas
       aparte para que nada quede invisible adentro de una lista.
    2. **¿Se puede ABRIR?** Si `1172` agrupa a otras cuentas, tiene que haber una
       forma de pedir el desglose. Se prueban las grafías de expansión y se
       compara contra la respuesta pelada: **más filas o claves nuevas = se
       abrió**; lo mismo = el parámetro se ignora, que es lo más probable
       (los cinco filtros de cuenta ya se ignoraron igual).
    """
    nombre = CUENTAS_CONTABLES.get(cod, "(desconocida)")
    print("\n" + "=" * 74)
    print(f"6) ADENTRO DE {cuenta} — contable {cod} ({nombre})")
    print("=" * 74)

    base = {"date": f, "accountTypeCode": cod}
    try:
        r = postrade.leer("AccountBalance", base)
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {str(e)[:200]}")
        return

    todas = _filas(r)
    mias = [x for x in todas
            if str(x.get("ClearingAccountCode", "")).strip() == str(cuenta)]
    print(f"  {len(todas)} filas en la contable · {len(mias)} con "
          f"ClearingAccountCode == {cuenta}")

    if not mias:
        print(f"\n  {cuenta} no está en esta contable. Lo que hay:")
        for x in todas:
            print(f"    {x.get('ClearingAccountCode','')!s:<12} "
                  f"{str(x.get('AccountOwner',''))[:40]}")
        return

    print("\n  ── EL JSON COMPLETO " + "─" * 50)
    for i, x in enumerate(mias, 1):
        print(f"\n  fila {i}/{len(mias)}:")
        print("  " + json.dumps(x, ensure_ascii=False, indent=2,
                                default=str).replace("\n", "\n  "))

    base_claves = _claves(mias[0])
    print(f"\n  ── TODAS las claves ({len(base_claves)}), anidadas incluidas ──")
    for k in sorted(base_claves):
        print(f"     {k}")

    print(f"\n  ── ¿SE PUEDE ABRIR {cuenta}? ── ({len(EXPANSIONES)} grafías)")
    abrio = False
    for clave in EXPANSIONES:
        try:
            rr = postrade.leer("AccountBalance", {**base, clave: "true"})
        except Exception as e:
            print(f"     ✗ «{clave}» → {type(e).__name__} (el parámetro no existe)")
            continue
        fl = _filas(rr)
        sus = [x for x in fl
               if str(x.get("ClearingAccountCode", "")).strip() == str(cuenta)]
        nuevas = set()
        for x in sus:
            nuevas |= _claves(x)
        extra = nuevas - base_claves
        if len(fl) != len(todas) or extra:
            abrio = True
            print(f"     ✔ «{clave}» CAMBIA la respuesta: {len(todas)}→{len(fl)} "
                  f"filas · claves nuevas: {sorted(extra) or '(ninguna)'}")
            for x in sus[:3]:
                print("       " + json.dumps(x, ensure_ascii=False, indent=2,
                                             default=str).replace("\n", "\n       "))
        else:
            print(f"     ~ «{clave}» devuelve lo mismo: lo ignora")

    if not abrio:
        print(f"\n  ⇒ Ninguna grafía abre {cuenta}. En `AccountBalance` la fila es")
        print("    ATÓMICA: un saldo por cuenta de compensación y moneda, sin")
        print("    desglose por comitente. Si el reporte necesita ver adentro, el")
        print("    dato NO está en este método — hay que pedirle a la cámara cuál")
        print("    lo abre (o cruzarlo por otro lado, p.ej. la posición).")


def _perfil(f: str, contables: list[str]) -> None:
    """Por cada CLAVE, los valores DISTINTOS que toma. El perfil de la respuesta.

    ⚠️ **Por qué hace falta, y qué se nos venía escapando** (2026-08-25): la fila
    trae DOS pares de campos de cuenta que veníamos leyendo como si fueran uno:

        AccountTypeCode      / AccountType         ← lo que pedimos por parámetro
        AccountingAccountCode / AccountingAccount  ← OTRA numeración

    Toda la exploración anterior los trató como sinónimos —`_mostrar` incluso
    rotula `AccountingAccountCode` como «cta. contable»— y eso importa porque
    `149667` matcheó justamente ahí. Mientras los dos digan lo mismo no pasa
    nada, y por eso no se notó: es REGLA #9(B) otra vez, dos campos sin árbitro.

    Un perfil por valores distintos lo contesta de una: si los dos pares tienen
    siempre el mismo valor, son lo mismo y no hay nada; si difieren, ahí vive la
    segunda numeración y ahí hay que buscar.
    """
    print("\n" + "=" * 74)
    print("7) PERFIL — qué valores toma CADA campo")
    print("=" * 74)

    for cod in contables:
        nombre = CUENTAS_CONTABLES.get(cod, "(desconocida)")
        print("\n" + "-" * 74)
        print(f"  contable {cod} — {nombre}")
        print("-" * 74)
        try:
            r = postrade.leer("AccountBalance", {"date": f, "accountTypeCode": cod})
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {str(e)[:200]}")
            continue

        filas = _filas(r)
        if not filas:
            print("  (vacía)")
            continue
        print(f"  {len(filas)} filas\n")

        claves: list[str] = []
        for x in filas:
            for k in x:
                if k not in claves:
                    claves.append(k)

        for k in claves:
            vals = sorted({str(x.get(k)) for x in filas if x.get(k) is not None})
            n = len(vals)
            muestra = ", ".join(vals[:8]) + (f" … +{n-8}" if n > 8 else "")
            print(f"    {k:<24} {n:>4} distinto(s)  {muestra[:120]}")

        # El chequeo que motivó todo esto: ¿los dos pares dicen lo mismo?
        pares = [("AccountTypeCode", "AccountingAccountCode"),
                 ("AccountType", "AccountingAccount")]
        for a, b in pares:
            difs = [x for x in filas
                    if str(x.get(a, "")).strip() != str(x.get(b, "")).strip()]
            if difs:
                print(f"\n    ⚠️ `{a}` y `{b}` DIFIEREN en {len(difs)}/{len(filas)} "
                      f"filas — son campos distintos, no sinónimos:")
                for x in difs[:5]:
                    print(f"       {a}={x.get(a)!r}  {b}={x.get(b)!r}  "
                          f"cta={x.get('ClearingAccountCode')!r}  "
                          f"titular={str(x.get('AccountOwner',''))[:26]}")
            else:
                print(f"\n    · `{a}` == `{b}` en las {len(filas)} filas "
                      f"(son el mismo dato duplicado)")


def _claves_margenes(f: str, habil: str) -> None:
    """TODAS las claves de `MarginRequirementReport`, en sus CUATRO niveles.

    ⚠️ **Por qué este sondeo antes de salir a buscar el activo integrado**:
    `aplanar_margenes` lee tres importes del último nivel (`Margin`,
    `OptionAmount`, `InterTempAmount`) y **descarta en silencio todo lo demás**.
    Si la cámara manda el integrado como un campo hermano del margen, ya lo
    tenemos y no hace falta ningún método nuevo — pero no se puede saber
    mirando el aplanado, porque el aplanado es justamente lo que lo tiró.

    Es el mismo modo de falla de siempre: no falla nada. El parser anda, los
    totales cierran, y el campo que necesitábamos nunca llegó a la base.

    Imprime, por nivel, las claves con un valor de ejemplo, y **marca las que el
    aplanado NO está leyendo**.
    """
    print("\n" + "=" * 74)
    print("8) CLAVES de MarginRequirementReport — los 4 niveles")
    print("=" * 74)

    crudo = None
    for fecha in (f, habil):
        try:
            r = postrade.leer("MarginRequirementReport", {"date": fecha})
        except Exception as e:
            print(f"  ✗ {fecha}: {type(e).__name__}: {str(e)[:150]}")
            continue
        if r:
            crudo, usada = r, fecha
            break
        print(f"  ~ {fecha}: contestó VACÍO")
    if not crudo:
        print("  Sin datos ni hoy ni el último hábil — no hay qué inventariar.")
        return
    print(f"  fecha con datos: {usada}\n")

    # Lo que el aplanado SÍ lee, por nivel. Escrito acá a mano a propósito: si
    # alguien agrega un campo al parser y no lo suma acá, la próxima corrida lo
    # marca como «NO se lee» y se nota. Un inventario que se deriva del parser
    # no puede delatar al parser.
    leidas = {
        1: {"Date", "ClearingMember", "ClearingMemberCode", "ProductGroup", "Accounts"},
        2: {"CompensationAccount", "CompensationAccountCode", "SubAccounts"},
        3: {"NettingAccountCode", "NettingAccount", "References"},
        4: {"Reference", "Currency", "Margin", "OptionAmount", "InterTempAmount"},
    }
    nombres = {1: "Value[] — agente", 2: "Accounts[] — cta. compensación",
               3: "SubAccounts[] — cta. de neteo", 4: "References[] — EL IMPORTE"}

    nivel: dict[int, dict[str, object]] = {1: {}, 2: {}, 3: {}, 4: {}}

    def _sumar(n: int, d) -> None:
        if isinstance(d, dict):
            for k, v in d.items():
                if k not in nivel[n] and not isinstance(v, (list, dict)):
                    nivel[n][k] = v
                elif k not in nivel[n]:
                    nivel[n][k] = f"<{type(v).__name__}>"

    for v1 in crudo if isinstance(crudo, list) else []:
        if not isinstance(v1, dict):
            continue
        _sumar(1, v1)
        for v2 in v1.get("Accounts") or []:
            if not isinstance(v2, dict):
                continue
            _sumar(2, v2)
            for v3 in v2.get("SubAccounts") or []:
                if not isinstance(v3, dict):
                    continue
                _sumar(3, v3)
                for v4 in v3.get("References") or []:
                    _sumar(4, v4)

    huerfanas: list[str] = []
    for n in (1, 2, 3, 4):
        print(f"  ── nivel {n}: {nombres[n]} " + "─" * 30)
        if not nivel[n]:
            print("     (ninguna — ese nivel no vino)")
            continue
        for k, ej in sorted(nivel[n].items()):
            if k in leidas[n]:
                print(f"     {k:<28} = {str(ej)[:40]}")
            else:
                huerfanas.append(f"nivel {n}: {k}")
                print(f"  ⚠️ {k:<28} = {str(ej)[:40]}   ← NO se lee")
        print()

    if huerfanas:
        print(f"  ⇒ {len(huerfanas)} campo(s) que la cámara manda y el parser TIRA:")
        for x in huerfanas:
            print(f"       {x}")
        print("     Si alguno es el ACTIVO INTEGRADO, ya lo tenemos: es sumarlo a")
        print("     `aplanar_margenes` y a la tabla, sin ningún método nuevo.")
    else:
        print("  ⇒ El parser lee TODO lo que viene. El activo integrado NO está")
        print("     en esta respuesta — hay que buscarlo en otro método.")


IMPORTES = ("margen", "primas", "inter_temporal")


def _referencias(f: str, habil: str, cuentas: list[str]) -> None:
    """Los CONCEPTOS de `Reference`, y en qué campo viaja el importe de cada uno.

    ⚠️ **`Reference` no es un identificador: es el NOMBRE del concepto**
    (`"Cauciones $"`). Y el importe **no siempre está en `Margin`** — se vio una
    fila con `Margin = 0.0` y el número en `InterTempAmount`.

    Las dos cosas juntas rompen la suma ingenua: totalizar `Margin` sobre todas
    las referencias devuelve un número **más chico**, y como los conceptos con
    margen 0 igual existen y suman 0, **no falla nada**. Sale un requerimiento
    creíble al que le falta la mitad.

    Así que antes de decidir qué suma la card hay que ver la grilla completa:
    qué conceptos hay, cuántas filas trae cada uno, y **cuáles de los tres
    importes usa** — porque de eso depende si «requerimiento de márgenes» es
    `Σ Margin`, `Σ(Margin+primas+inter)`, o solo algunos conceptos.

    Y de paso: si alguno de los conceptos ES el activo integrado, está acá.
    """
    print("\n" + "=" * 74)
    print("9) CONCEPTOS (`Reference`) — qué hay y en qué campo viene el importe")
    print("=" * 74)

    crudo, usada = None, ""
    for fecha in (f, habil):
        try:
            r = postrade.leer("MarginRequirementReport", {"date": fecha})
        except Exception as e:
            print(f"  ✗ {fecha}: {type(e).__name__}")
            continue
        if r:
            crudo, usada = r, fecha
            break
        print(f"  ~ {fecha}: VACÍO")
    if not crudo:
        print("  Sin datos.")
        return

    filas, _ = aplanar_margenes(crudo)
    print(f"  fecha {usada} · {len(filas)} referencias\n")

    # concepto → moneda → {campo: total, n}
    grilla: dict[tuple[str, str], dict] = {}
    for x in filas:
        k = (x.get("referencia") or "(sin nombre)", x.get("moneda") or "(sin moneda)")
        d = grilla.setdefault(k, {"n": 0, **{c: 0.0 for c in IMPORTES},
                                  "nonulo": {c: 0 for c in IMPORTES}})
        d["n"] += 1
        for c in IMPORTES:
            v = x.get(c)
            if v:
                d[c] += float(v)
                d["nonulo"][c] += 1

    print(f"  {'concepto':<26} {'moneda':<10} {'filas':>6} "
          f"{'margen':>17} {'primas':>15} {'inter_temporal':>17}")
    for (ref, mon), d in sorted(grilla.items()):
        print(f"  {ref[:26]:<26} {mon:<10} {d['n']:>6} "
              f"{d['margen']:>17,.2f} {d['primas']:>15,.2f} "
              f"{d['inter_temporal']:>17,.2f}")

    print("\n  ── QUÉ CAMPO USA CADA CONCEPTO ──")
    for (ref, mon), d in sorted(grilla.items()):
        usa = [c for c in IMPORTES if d["nonulo"][c]]
        aviso = "  ⚠️ el importe NO está en `margen`" if usa and "margen" not in usa else ""
        print(f"  {ref[:26]:<26} {mon:<10} → {', '.join(usa) or 'TODOS EN CERO'}{aviso}")

    # Lo que la card muestra HOY contra lo que mostraría sumando los tres.
    print("\n  ── LO QUE CAMBIA EL CRITERIO ──")
    por_mon: dict[str, dict] = {}
    for x in filas:
        d = por_mon.setdefault(x.get("moneda") or "(sin moneda)",
                               {c: 0.0 for c in IMPORTES})
        for c in IMPORTES:
            d[c] += float(x.get(c) or 0)
    print(f"  {'moneda':<12} {'solo margen (HOY)':>22} {'margen+primas+inter':>24}")
    for mon, d in sorted(por_mon.items()):
        print(f"  {mon:<12} {d['margen']:>22,.2f} "
              f"{sum(d[c] for c in IMPORTES):>24,.2f}")
    print("\n  Si las dos columnas dan distinto, la card de hoy está incompleta.")

    if cuentas:
        print(f"\n  ── SOLO LAS CUENTAS PEDIDAS {cuentas} ──")
        mias = [x for x in filas if str(x.get("cuenta")) in set(cuentas)]
        print(f"  {len(mias)} referencias")
        print(f"  {'cuenta':<10} {'comp.':<9} {'concepto':<24} {'moneda':<9} "
              f"{'margen':>15} {'primas':>13} {'inter':>15}")
        for x in sorted(mias, key=lambda y: (y["cuenta"], y["moneda"],
                                             y["referencia"] or "")):
            print(f"  {x['cuenta']:<10} "
                  f"{(x.get('cuenta_compensacion_codigo') or '')[:9]:<9} "
                  f"{(x.get('referencia') or '')[:24]:<24} "
                  f"{x['moneda']:<9} {float(x.get('margen') or 0):>15,.2f} "
                  f"{float(x.get('primas') or 0):>13,.2f} "
                  f"{float(x.get('inter_temporal') or 0):>15,.2f}")

        # El total de ESAS cuentas con los dos criterios, que es la decisión.
        print(f"\n  {'moneda':<10} {'solo margen (la card HOY)':>28} "
              f"{'los tres importes':>22}")
        pm: dict[str, dict] = {}
        for x in mias:
            d = pm.setdefault(x["moneda"] or "(sin moneda)",
                              {c: 0.0 for c in IMPORTES})
            for c in IMPORTES:
                d[c] += float(x.get(c) or 0)
        for mon, d in sorted(pm.items()):
            print(f"  {mon:<10} {d['margen']:>28,.2f} "
                  f"{sum(d[c] for c in IMPORTES):>22,.2f}")


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
    ap.add_argument("--crudo", default="",
                    help="volcar el JSON CRUDO de estas cuentas, separadas por "
                         "coma (ej. 218115,149667). Busca en TODOS los campos, "
                         "porque cada cuenta se identifica por uno distinto.")
    ap.add_argument("--contables", default="11,14,21,22",
                    help="en qué cuentas contables mirar el --crudo "
                         "(default 11,14,21,22).")
    ap.add_argument("--referencias", action="store_true",
                    help="los CONCEPTOS de `Reference` y en qué campo viene el "
                         "importe de cada uno. Combinar con --cuentas.")
    ap.add_argument("--claves", action="store_true",
                    help="inventario de TODAS las claves de "
                         "MarginRequirementReport en sus 4 niveles, marcando "
                         "las que el parser NO está leyendo.")
    ap.add_argument("--perfil", action="store_true",
                    help="por cada campo, los valores distintos que toma en "
                         "--contables. Es el mapa de qué significa cada clave.")
    ap.add_argument("--adentro", default="",
                    help="TODO lo que devuelve ESTA cuenta en --contables (la "
                         "primera), y si se la puede abrir por sub-cuenta.")
    ap.add_argument("--buscar", default="",
                    help="buscar este/estos código(s) en TODOS los campos de "
                         "todas las filas (ej. 149667). Recorre LAS 29 "
                         "contables salvo que pases --contables.")
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
    contables = [c.strip() for c in args.contables.split(',') if c.strip()]

    # `--crudo` es EXCLUYENTE: cuando se pide el volcado, se pide eso y nada
    # más. Mezclarlo con los otros bloques entierra el JSON en 300 líneas.
    if args.referencias:
        _referencias(f, habil, cuentas)
    elif args.claves:
        _claves_margenes(f, habil)
    elif args.perfil:
        _perfil(f, contables)
    elif args.adentro:
        _adentro(f, args.adentro, contables[0] if contables else "14")
    elif args.buscar:
        agujas = [a.strip() for a in args.buscar.split(',') if a.strip()]
        # Sin --contables explícito, buscar es buscar EN TODAS: acotar el
        # barrido a cuatro y después decir «no está» sería exactamente el error
        # que este bloque existe para no cometer.
        todas_cont = (contables if "--contables" in " ".join(sys.argv)
                      else list(CUENTAS_CONTABLES))
        _buscar(f, agujas, todas_cont)
    elif args.crudo:
        _crudo(f, [c.strip() for c in args.crudo.split(',') if c.strip()],
               contables)
    else:
        if not args.solo_barrido:
            _margenes(f, habil, cuentas)
            _saldos(f, args.cuenta_contable, cuentas)
        if args.barrer or args.solo_barrido:
            _barrer(f, cuentas)

    if args.crudo or args.buscar or args.adentro or args.perfil or args.claves or args.referencias:
        return
    print("\n" + "=" * 74)
    print("Qué mirar:")
    print("  · VACÍO ≠ ERROR. Vacío es «el método anda y no hay dato».")
    print("  · El GRANO de AccountBalance: la fila es del ALyC, no del comitente.")
    print("  · La MONEDA de cada importe — no se suman entre sí.")
    print("  · Cuentas contables distintas TAMPOCO se suman entre sí.")


if __name__ == "__main__":
    main()
