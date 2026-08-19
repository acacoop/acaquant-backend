"""scripts/diag_pata_dolar.py — ¿CUÁNTOS de los «cotiza en pesos» tienen pata USD?

Doc madre: `docs/AV_AGENT.md` §0.x. **READ-ONLY**: no escribe una sola fila.

POR QUÉ EXISTE
==============

El hallazgo `cotiza_en_pesos` cerraba con *«No encontré una pata en dólares para
este ticker»* después de mirar UNA tabla (`mercado.especies`), que se siembra a
mano. Ahora mira también el catálogo de Primary — pero **cuántos de los 43 casos
cambian de veredicto con eso es un dato de prod, y no se puede suponer**
(REGLA #2). Este diag lo mide.

Contesta, por bono de curva USD que hoy cotiza en pesos:

    sembrada          ya tenemos la pata D/C en `mercado.especies`
    solo_en_primary   existe en Primary y NO la teníamos
    por_ficha         el NOMBRE no se parece, pero Primary dice que es el mismo
                      bono (mismo `underlying` + `maturity`)  ← el caso BOPREAL
    sin_pata          no está por ninguna de las tres vías: es el instrumento

Y para cada una, **si la estamos escuchando**, que son TRES estados y no dos —
la distinción del AO29 (§0.v):

    NO ESCUCHA   la pata no está en `market_snapshot`: nadie la suscribe, así que
                 su falta de precio NO prueba nada
    SIN PUNTA    está en el snapshot y sin precio: la escuchamos y el mercado no
                 dio punta → eso SÍ es iliquidez
    <precio>     cotiza, y sabemos a cuánto

⚠️ Los DÓLAR LINKED quedan fuera: cotizan en pesos **por definición** (se
denominan en USD y pagan en pesos), así que no tienen ni van a tener pata en
dólares. Contarlos como casos era el 18% de la lista en ruido estructural.

Uso:
    python -m scripts.diag_pata_dolar            # el resumen + las primeras 40
    python -m scripts.diag_pata_dolar --todos    # todas las filas
    python -m scripts.diag_pata_dolar AO29 CO32  # solo esos tickers
"""
from __future__ import annotations

import sys

from api.services import av_agent
from core import especies as E
from core.postgres import get_pool

_SUF = ("D", "C")


def _corto(sim: str) -> str:
    p = (sim or "").split(" - ")
    return p[2].strip().upper() if len(p) >= 3 else (sim or "").strip()


def main() -> int:
    pedidos = {a.strip().upper() for a in sys.argv[1:] if not a.startswith("--")}
    todos = "--todos" in sys.argv

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, instrumento, curva, ajuste, ajuste_alt "
                    "FROM mercado.curvas "
                    "WHERE upper(coalesce(moneda_eje, '')) = 'USD' "
                    "  AND instrumento IS NOT NULL ORDER BY ticker")
        bonos = [(r[0], r[1], r[2], r[3], r[4]) for r in cur.fetchall()]

        cur.execute("SELECT upper(ticker), simbolo FROM mercado.especies "
                    "WHERE upper(moneda) = 'USD'")
        sembradas: dict[str, list[str]] = {}
        for tk, sim in cur.fetchall():
            sembradas.setdefault(tk, []).append(sim)

        # **TODO el snapshot, con precio o sin él.** Filtrar por `last_price > 0`
        # era el error de origen: dejaba indistinguibles «nadie la suscribe» y
        # «la suscribimos y no dio punta», que es justo la diferencia que importa.
        cur.execute("SELECT ticker, last_price FROM mercado.market_snapshot")
        snap = {r[0]: (float(r[1]) if r[1] is not None else None)
                for r in cur.fetchall()}
        precios = {k: v for k, v in snap.items() if v}

    primary = av_agent.simbolos_primary()
    if primary is None:
        print("\n  ⚠️  No se pudo leer el catálogo de Primary "
              "(`manager.pyrofex_instruments`).\n"
              "      SIN ÉL ESTE DIAG NO PUEDE CONCLUIR NADA — que es justo el "
              "punto del fix.\n"
              "      Refrescarlo: python -m scripts.discovery_pyrofex\n")
        return 1

    # La ficha de cada símbolo, para la tercera vía (ver el docstring). Si no se
    # puede leer, se degrada: la vía no corre y se DICE, no se finge que no había.
    try:
        instrumentos = E.instrumentos_primary()
    except Exception as e:
        print(f"\n  ⚠️  no pude leer las fichas de Primary ({type(e).__name__}): "
              f"la vía `por_ficha` NO corre y los `sin_pata` de abajo pueden "
              f"tener pata igual.\n")
        instrumentos = []

    filas = []
    resumen = {"sembrada": 0, "solo_en_primary": 0, "por_ficha": 0, "sin_pata": 0}
    escucha = {"no_escucha": 0, "sin_punta": 0, "con_precio": 0}
    n_dl = 0
    for tk, simbolo, curva, ajuste, ajuste_alt in bonos:
        TK = (tk or "").strip().upper()
        if pedidos and TK not in pedidos:
            continue
        # Los dólar linked NO son casos: pagan en pesos por definición.
        if "dolar_linked" in {(ajuste or "").strip().lower(),
                              (ajuste_alt or "").strip().lower(),
                              (curva or "").strip().lower()}:
            n_dl += 1
            continue
        base = _corto(simbolo)
        # ¿Cotiza HOY por una pata que no es de dólares? El precio en pesos es lo
        # que dispara el hallazgo; sin precio el caso es otro (`sin_precio`).
        px = precios.get(simbolo)
        if px is None:
            continue
        if base[-1:].upper() in _SUF:
            continue                      # ya cotiza por su pata en dólares

        mias = sembradas.get(TK) or []
        en_primary = [x for suf in _SUF for x in primary if f" - {base}{suf} - " in x]
        nuevas = [x for x in en_primary if x not in set(mias)]

        # TERCERA VÍA: el nombre no se parece, pero Primary dice que es el mismo
        # bono. Solo se pregunta si las dos primeras fallaron — es más cara y no
        # aporta nada donde el string ya alcanzó.
        por_ficha = ([] if (mias or nuevas) else
                     [h["simbolo"] for h in
                      E.hermanas_por_ficha(base, instrumentos, moneda="USD")])
        origen = ("sembrada" if mias else
                  "solo_en_primary" if nuevas else
                  "por_ficha" if por_ficha else "sin_pata")
        resumen[origen] += 1
        cand = (sorted(mias) or sorted(nuevas) or por_ficha or [""])[0]
        # LOS TRES ESTADOS. `cand in snap` es «está en el snapshot», con precio o
        # sin él — que es distinto de `cand in precios`.
        # `cand in snap` = está en el snapshot (con precio o sin él). El adhoc no
        # hace falta mirarlo: el motor puede estar suscribiendo la pata desde el
        # master o desde el universo de portfolio sin ningún adhoc, así que lo que
        # prueba que la escuchamos es que la fila EXISTA, no cómo se pidió.
        est = ("—" if not cand else
               "con_precio" if precios.get(cand) else
               "sin_punta" if cand in snap else "no_escucha")
        if cand:
            escucha[est] = escucha.get(est, 0) + 1
        filas.append((TK, base, origen, _corto(cand) if cand else "—",
                      est, f"{precios[cand]:,.2f}" if precios.get(cand) else "—",
                      curva or "—"))

    n = len(filas)
    print(f"\n{'=' * 82}\nBONOS DE CURVA USD QUE HOY COTIZAN EN PESOS: {n}\n{'=' * 82}")
    print(f"  (+{n_dl} dólar linked excluidos: cotizan en pesos POR DEFINICIÓN)\n")
    print("  ¿EXISTE LA PATA EN DÓLARES?")
    for k, v in resumen.items():
        pct = f"{v / n * 100:5.1f}%" if n else "    —"
        print(f"    {k:<18} {v:>4}  {pct}")
    if resumen["por_ficha"]:
        print("\n    → `por_ficha` son los que NINGUNA regla de nombre encuentra "
              "y existen\n      igual (el caso BOPREAL: BPOA7 → BPA7D). Los "
              "emparejó el JOIN por\n      underlying+maturity, sin adivinar "
              "nada del string.")
    print("\n  ¿LA ESTAMOS ESCUCHANDO?  (de los que TIENEN pata)")
    m = sum(escucha.values())
    for k, v in (("no_escucha", escucha["no_escucha"]),
                 ("sin_punta", escucha["sin_punta"]),
                 ("con_precio", escucha["con_precio"])):
        pct = f"{v / m * 100:5.1f}%" if m else "    —"
        print(f"    {k:<18} {v:>4}  {pct}")
    print("\n  → `no_escucha` es lo accionable: la pata existe y nadie la pide, "
          "así que\n    su falta de precio NO prueba nada. Se arregla en el acto "
          "(adhoc, 5s).\n  → `sin_punta` NO es trabajo: la pedimos y el mercado "
          "no dio punta.\n")

    if filas:
        print(f"{'TICKER':<9}{'PIDE HOY':<11}{'PATA':<18}"
              f"{'PATA USD':<11}{'ESCUCHA':<12}{'PRECIO':<12}CURVA")
        print("-" * 82)
        for f in (filas if todos else filas[:40]):
            print(f"{f[0]:<9}{f[1]:<11}{f[2]:<18}{f[3]:<11}{f[4]:<12}{f[5]:<12}{f[6]}")
        if not todos and n > 40:
            print(f"\n  … {n - 40} más. Verlas todas: --todos")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
