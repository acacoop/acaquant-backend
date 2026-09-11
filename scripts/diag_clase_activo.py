"""`scripts/diag_clase_activo.py` — **POR QUÉ NO SE PROPONE LA CLASE DE ACTIVO.**

Read-only. Corre **la misma cadena que la pantalla** (`agente.clase.proponer`
con las mismas fuentes que le pasa `arreglos.CompletarFicha.preview`) sobre los
mismos faltantes, y parte el resultado por CAUSA.

Existe porque en el listado de `clase_activo` **una fila sin propuesta no dice
por qué**: «ninguna regla aplica», «la regla derivó un valor que no está en la
lista cerrada», «el nombre no matcheó la ficha de Primary» y «el bono no está en
el master» se ven EXACTAMENTE IGUAL — un guion. Y son cuatro trabajos distintos:
uno es cargar un valor una vez, otro es un bug de matcheo, otro es que no hay
nada que proponer.

⚠️ **NO REIMPLEMENTA NINGUNA REGLA.** Llama a `agente.clase.proponer` y después
vuelve a preguntarle a las MISMAS funciones puras por qué dijeron "" — si acá
hubiera una copia del criterio, el diag podría contestar distinto que la
pantalla y no fallaría nada (REGLA #9).

    python -m scripts.diag_clase_activo
    python -m scripts.diag_clase_activo --detalle            # fila por fila
    python -m scripts.diag_clase_activo --cartera FCI        # solo esa cartera
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

from agente import clase, fuentes
from agente.detectores import catalogo as det
from core.clase_activo import (
    _RE_OTC,
    _RE_PREFIJO,
    POR_AJUSTE,
    PRODUCTO,
    _sin_corchetes,
    normalizar_nombre,
)
from core.postgres import get_pool

# Las carteras que el código trata especial, para poder decir «ninguna regla
# cubre esta cartera» sin inventar una lista nueva.
_FCI = clase._CARTERAS_FCI


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def _prefijo(unidad: str, ticker: str) -> str:
    """Las 3 letras del contrato, SOLO PARA MOSTRAR — con las mismas regex que
    usa `de_futuro` para decidir, no con un parseo propio."""
    for cand in (unidad, ticker):
        c = _sin_corchetes(cand or "").upper()
        if not c:
            continue
        m_otc = _RE_OTC.match(c)
        contrato = m_otc.group(1) if m_otc else c
        if (m := _RE_PREFIJO.match(contrato)):
            return m.group(1)
    return "—"


def _por_que_vacio(f: dict, indice_primary: dict, indice_master: dict,
                   primary_leido: bool) -> tuple[str, str]:
    """`(causa, detalle)` de una fila que quedó sin propuesta y sin nota.

    La causa se deduce preguntándole a las mismas fuentes que miró `proponer`.
    """
    cartera = (f.get("cartera") or "").strip().upper()
    unidad, ticker = f.get("unidad", ""), f.get("ticker", "")

    if not cartera or cartera == "NO APLICA":
        return ("SIN CARTERA", "las 5 reglas arrancan mirando la cartera: "
                               "sin ella ninguna se puede ni evaluar")

    if cartera == "DERIVADOS":
        p = _prefijo(unidad, ticker)
        if p == "—":
            return ("DERIVADOS · el contrato no parsea",
                    f"ni la unidad ni el ticker tienen la forma XXX.YYY/... → {unidad[:40]}")
        if p not in PRODUCTO:
            return (f"DERIVADOS · prefijo «{p}» desconocido",
                    "no está en core.clase_activo.PRODUCTO (SOJ/SOY/MAI/CRN/TRI/DLR)")
        return (f"DERIVADOS · «{p}» es dólar sin OTC",
                "el dólar solo se propone bajo OTC (decisión declarada)")

    if cartera in _FCI:
        if not primary_leido:
            return ("FCI · no pude leer Primary",
                    "fichas_primary() devolvió None: el catálogo no se pudo leer")
        clave = normalizar_nombre(ticker or "")
        ficha = indice_primary.get(clave)
        if not ficha:
            return ("FCI · el nombre no matchea ninguna ficha de Primary",
                    f"buscó la clave «{clave[:48]}»")
        return ("FCI · la ficha no mapea a una clase",
                f"tipo_renta «{ficha.get('subyacente', '')}» / "
                f"moneda «{ficha.get('moneda', '')}»")

    if cartera == "ARS":
        doc = indice_master.get((ticker or "").strip().upper())
        if not doc:
            return ("ARS · el bono no está en mercado.curvas",
                    f"no hay doc con ticker_corto = «{(ticker or '').strip().upper()}»")
        me = (doc.get("moneda_eje") or "").strip().upper()
        if me != "ARS":
            return (f"ARS · la curva dice moneda_eje «{me or '(vacío)'}»",
                    "la regla solo opina si los EJES dicen ARS")
        aj = (doc.get("ajuste") or "").strip().lower()
        if aj not in POR_AJUSTE:
            return (f"ARS · ajuste «{aj or '(sin ejes)'}» no tiene clase",
                    "POR_AJUSTE solo cubre cer/fija/tamar (badlar, tpm, "
                    "caucion y dolar_linked no se proponen, a propósito)")
        return ("ARS · caso no previsto", f"ajuste {aj}, moneda_eje {me}")

    return (f"cartera «{cartera}» sin regla",
            "ninguna de las 5 reglas mira esta cartera")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detalle", action="store_true", help="fila por fila")
    ap.add_argument("--cartera", default="", help="filtrar por cartera")
    a = ap.parse_args()

    campo = next(c for c in det.CAMPOS if c["campo"] == "clase_activo")
    filas = det.faltantes(campo)
    if a.cartera:
        filas = [f for f in filas
                 if (f.get("cartera") or "").strip().upper() == a.cartera.strip().upper()]
    usadas = det.valores_usados("clase_activo")
    fichas = fuentes.fichas_primary()
    master = fuentes.master()

    _titulo("EL UNIVERSO")
    print(f"  {len(filas)} título(s) en cartera de cliente sin clase_activo"
          + (f" (filtrado por cartera = {a.cartera})" if a.cartera else ""))
    print(f"  lista cerrada: {len(usadas)} valor(es) ya cargados en clase_activo")
    print(f"  Primary: {'—' if fichas is None else len(fichas)} ficha(s)"
          f"   ·   master de curvas: {'—' if master is None else len(master)} bono(s)")
    if fichas is None:
        print("  ⚠ fichas_primary() = None → la regla de FCI no puede proponer NADA")
    print("\n  POR CARTERA:")
    for c, n in Counter((f.get("cartera") or "(vacía)").strip().upper()
                        for f in filas).most_common():
        print(f"    {c:28} {n:>4}")

    # La MISMA llamada que hace la pantalla.
    propuestas = clase.proponer(filas, fichas, usadas, master=master)
    indice_primary = clase._indice_primary(fichas or [])
    indice_master = clase._indice_master(master)

    con, notas, vacios = [], defaultdict(list), defaultdict(list)
    for f in propuestas:
        if f["propuesto"]:
            con.append(f)
        elif f["nota"]:
            # El valor que la regla derivó y la lista cerrada rechazó.
            valor = f["nota"].split("«")[1].split("»")[0] if "«" in f["nota"] else "?"
            notas[valor].append(f)
        else:
            causa, detalle = _por_que_vacio(f, indice_primary, indice_master,
                                            fichas is not None)
            vacios[(causa, detalle)].append(f)

    _titulo("EL VEREDICTO")
    print(f"  ✔ SE PROPONEN            {len(con):>4}   ← el ejecutor las escribe solo")
    print(f"  ⚠ FALTA CARGAR EL VALOR  {sum(len(v) for v in notas.values()):>4}"
          f"   ← la regla SABE, el valor no existe todavía")
    print(f"  ✘ NINGUNA REGLA APLICA   {sum(len(v) for v in vacios.values()):>4}"
          f"   ← hoy la pantalla no dice por qué")

    if con:
        print("\n  ── LO QUE SE PROPONE, por fuente ──")
        for (fu, v), n in Counter((f["fuente"], f["propuesto"])
                                  for f in con).most_common():
            print(f"    {fu:8} {v:24} {n:>4}")

    if notas:
        _titulo("⚠ LA REGLA SABE Y EL VALOR NO EXISTE — un alta a mano lo desbloquea")
        for valor, fs in sorted(notas.items(), key=lambda kv: -len(kv[1])):
            print(f"  «{valor}» → {len(fs)} título(s)")
            print(f"      {', '.join((f.get('ticker') or f['unidad'])[:22] for f in fs[:6])}"
                  + (" …" if len(fs) > 6 else ""))
        print("\n  → cargando UNO a mano en clase_activo, el ejecutor completa el resto")

    _titulo("✘ NINGUNA REGLA APLICA — por causa")
    for (causa, detalle), fs in sorted(vacios.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  {len(fs):>4}  {causa}")
        print(f"        {detalle}")
        print(f"        {', '.join((f.get('ticker') or f['unidad'])[:22] for f in fs[:8])}"
              + (" …" if len(fs) > 8 else ""))

    # ── ¿LA RESPUESTA YA ESTÁ EN LA BASE? (solo FCI) ───────────────────────
    #
    # `mercado.fci` la arma `jobs/fci_universo` con la MISMA función
    # (`core.clase_activo.de_fci`, vía `fci_match.sugerir_categoria`), ya trae
    # `tipo_renta`/`moneda` de Primary y —lo que importa— el LINK `unidad` al
    # asset, que es la clave con la que se escribe. Si estos números dan alto,
    # la regla del agente está preguntándole a la fuente equivocada.
    fci = [f for f in filas
           if (f.get("cartera") or "").strip().upper() in _FCI]
    if fci:
        _titulo("FCI — ¿la respuesta ya está en mercado.fci?")
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT unidad, nombre, tipo_renta, moneda, categoria "
                "  FROM mercado.fci WHERE unidad = ANY(%s)",
                ([f["unidad"] for f in fci],))
            enlazados = {r[0]: r[1:] for r in cur.fetchall()}
        con_tipo = sum(1 for v in enlazados.values() if (v[1] or "").strip())
        con_cat = sum(1 for v in enlazados.values() if (v[3] or "").strip())
        print(f"  {len(fci)} FCI sin clase_activo")
        print(f"  {len(enlazados)} de ellos YA están linkeados en mercado.fci "
              f"(por `unidad`, la misma clave con la que se escribe)")
        print(f"  {con_tipo} con `tipo_renta` de Primary   ·   "
              f"{con_cat} con `categoria` ya sugerida por el job")
        if enlazados:
            print("\n  Ejemplos (lo que el agente buscó vs. lo que la base ya tiene):")
            for f in fci[:6]:
                v = enlazados.get(f["unidad"])
                clave = normalizar_nombre(f.get("ticker") or "")
                print(f"    {f['unidad'][:34]:34}")
                print(f"        agente buscó por nombre: «{clave[:46]}» → "
                      f"{'MATCHEÓ' if indice_primary.get(clave) else 'NO matcheó'}")
                if v:
                    print(f"        mercado.fci ya dice:     tipo_renta «{v[1] or '—'}» · "
                          f"moneda «{v[2] or '—'}» · categoria «{v[3] or '—'}»")
                else:
                    print("        mercado.fci: no hay fila con esa unidad")

    if a.detalle:
        _titulo("FILA POR FILA")
        for f in propuestas:
            estado = (f"✔ {f['fuente']}:{f['propuesto']}" if f["propuesto"]
                      else ("⚠ nota" if f["nota"] else "✘"))
            print(f"  {estado:34} {(f.get('cartera') or '—')[:14]:14} "
                  f"{(f.get('ticker') or '')[:20]:20} {f['unidad'][:46]}")

    print("\n  Read-only: no escribió nada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
