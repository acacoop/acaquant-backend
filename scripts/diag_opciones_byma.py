"""scripts/diag_opciones_byma.py — ¿cómo están cargadas las OPCIONES SOBRE
ACCIONES en `portafolio.assets`, y qué clase les puso la mesa?

Doc madre: docs/AGENT.md §0.fn (habilidad `ficha_incompleta`, regla
`sin_clase_activo`). READ-ONLY: no escribe nada.

El listado de `completar_ficha` tiene contratos que ninguna regla de derivados
reconoce (`GFGC4600DI`): la regex de hoy sabe leer la forma agro/OTC
(`SOJ.ROS/MAY27 340 C`) y nada más. La HIPÓTESIS a verificar es que esos
tickers siguen la nomenclatura de opciones de BYMA sobre acciones:

    <3-4 letras del subyacente> <C|V> <strike> <2 letras del mes>
    GFG C 4600 DI   →  Galicia, compra (call), 4600, diciembre

**No se codea la regla hasta medirla contra lo que la mesa YA cargó** (REGLA
#2): este script lista todos los assets de cartera DERIVADOS cuyo ticker o
unidad tiene esa forma, cruza la letra C/V con la `clase_activo` que ya tienen
(CALL/PUT OPCIONES o vacío) y muestra los sufijos de mes que aparecen. Si la
letra y la clase cargada coinciden en todos los que tienen clase, la regla se
escribe con test; si no, hay que entender por qué antes.

Uso:
    python -m scripts.diag_opciones_byma            # todos los DERIVADOS
    python -m scripts.diag_opciones_byma --todos    # también los que no matchean, para ver qué más hay
"""
from __future__ import annotations

import argparse
import re
from collections import Counter

from core.postgres import get_pool

_RE_BYMA = re.compile(r"^([A-Z]{3,4})([CV])(\d+(?:[.,]\d+)?)([A-Z]{2})$")


def _limpio(s: str | None) -> str:
    s = (s or "").strip().upper()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    return s


def _parsear(unidad: str, ticker: str):
    for c in (unidad, ticker):
        m = _RE_BYMA.match(_limpio(c))
        if m:
            return m.groups()
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--todos", action="store_true", help="imprime también los que NO matchean")
    a = ap.parse_args()

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT a.unidad, a.ticker, a.clase_activo,"
            " EXISTS (SELECT 1 FROM portafolio.tenencia t WHERE t.unidad = a.unidad"
            "         AND t.fecha = (SELECT max(fecha) FROM portafolio.tenencia)) AS en_cartera"
            " FROM portafolio.assets a WHERE upper(btrim(coalesce(a.cartera, ''))) = 'DERIVADOS'"
            " ORDER BY a.unidad")
        filas = cur.fetchall()

    print(f"DERIVADOS en portafolio.assets: {len(filas)}")
    matchean, otros = [], []
    for unidad, ticker, clase, en_cartera in filas:
        p = _parsear(unidad, ticker)
        (matchean if p else otros).append((unidad, ticker, (clase or "").strip(), bool(en_cartera), p))

    print(f"con forma BYMA <sub><C|V><strike><mes>: {len(matchean)}  ·  otros: {len(otros)}\n")

    # Letra vs clase cargada: la verificación que decide si la regla se escribe.
    cruce: Counter = Counter()
    for _u, _t, clase, _e, (_sub, letra, _strike, _mes) in matchean:
        cruce[(letra, clase or "(sin clase)")] += 1
    print("letra C/V × clase_activo cargada:")
    for (letra, clase), n in sorted(cruce.items()):
        print(f"  {letra}  {clase:22} {n:4}")
    meses = Counter(mes for *_, (_s, _l, _k, mes) in matchean)
    print("\nsufijos de mes vistos:", ", ".join(f"{m}={n}" for m, n in sorted(meses.items())))
    subs = Counter(sub for *_, (sub, _l, _k, _m) in matchean)
    print("subyacentes:", ", ".join(f"{s}={n}" for s, n in subs.most_common(20)))

    print("\nCON forma BYMA y CON clase (lo que la mesa ya decidió: la letra tiene que coincidir):")
    for unidad, ticker, clase, en_cartera, (sub, letra, strike, mes) in matchean:
        if clase:
            print(f"  {'●' if en_cartera else '○'} {unidad:40} {ticker:16} {sub} {letra} {strike} {mes}"
                  f"   clase={clase}")

    print("\nCON forma BYMA y SIN clase (los que la regla resolvería):")
    for unidad, ticker, clase, en_cartera, (sub, letra, strike, mes) in matchean:
        if not clase:
            print(f"  {'●' if en_cartera else '○'} {unidad:40} {ticker:16} {sub} {letra} {strike} {mes}")
    print("  (● = en cartera de cliente hoy)")

    if a.todos:
        print("\nDERIVADOS que NO tienen forma BYMA (para ver qué otras formas hay):")
        for unidad, ticker, clase, en_cartera, _p in otros:
            print(f"  {'●' if en_cartera else '○'} {unidad:40} {ticker:16} clase={clase or '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
