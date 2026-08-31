"""READ-ONLY. ¿Con qué grafía publica 1816 la pata que nos falta?

Herramienta: diag · Primero el catálogo (gratis), después la API (cuesta).

EL PUNTO
========

`jobs/tamar_1816` pide la pata fija de TTD26/TTS26 con DOS grafías y 1816 no
devuelve TEA en ninguna. Las otras tres duales (TMVE8, TXMD8, TXMD9) andan
perfecto, así que el mecanismo funciona: falla en estos dos.

Y hay un sospechoso. El job lee la grafía del catálogo para las variantes
principales —lo que su propio docstring exige, *«la grafía la manda el CATÁLOGO
de 1816, no nosotros»*— pero para el ALIAS la **concatena**:

    pedidos[f"{tk} @{suf_den}"] = ...        # "TTD26 @BONCAP"

Si 1816 la publica con otro espaciado, otro orden, o directamente bajo otro
ticker, ese pedido vuelve vacío **sin error**: es la misma clase de falla contra
la que el job se defiende en el resto del archivo.

PASO 1 — GRATIS. Busca en `research.mkt_1816_instrumentos` **por denominación**,
no por ticker: si la pata existe bajo una grafía que no adivinamos, acá aparece.
El catálogo lo llena `jobs/mercado_1816_discovery --catalogo`, que recorre las 28
curvas — o sea que ve TODO lo que 1816 publica, no solo lo que le pedimos.

PASO 2 — CUESTA CRÉDITOS, y por eso es opt-in (`--pedir`). Le pregunta a 1816 por
cada grafía candidata y dice cuál trae TEA. Una llamada para todas.

Uso:
    python -m scripts.diag_1816_grafias TTD26 TTS26           # solo catálogo
    python -m scripts.diag_1816_grafias TTD26 TTS26 --pedir   # + preguntar
"""
from __future__ import annotations

import sys

from core.postgres import get_job_pool as get_pool


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def main() -> int:
    tickers = [a.upper() for a in sys.argv[1:] if not a.startswith("-")]
    if not tickers:
        print(__doc__)
        return 2

    print(f"\n{'=' * 78}\n QUÉ PUBLICA 1816 DE ESTOS BONOS\n{'=' * 78}\n")

    candidatas: list[str] = []
    for tk in tickers:
        # POR DENOMINACIÓN, no por ticker: es la única forma de encontrar una
        # variante cuya grafía de ticker no adivinamos.
        filas = _q("""
            SELECT ticker, denominacion FROM research.mkt_1816_instrumentos
             WHERE denominacion ILIKE %s OR ticker ILIKE %s
             ORDER BY ticker
        """, (f"%{tk}%", f"%{tk}%"))
        print(f"  ── {tk}: {len(filas)} entrada(s) en el catálogo de 1816\n")
        if not filas:
            print("     ← 1816 no lo tiene en su catálogo. No hay de dónde traer nada.\n")
            continue
        for f in filas:
            print(f"     ticker: {str(f['ticker'])!r}")
            print(f"     denom : {str(f['denominacion'])!r}\n")
            candidatas.append(str(f["ticker"]))

    if "--pedir" not in sys.argv:
        print("  Para preguntarle a 1816 cuál de estas grafías trae TEA (cuesta\n"
              "  créditos, una sola llamada):  agregá `--pedir`\n")
        return 0

    # PASO 2 — se piden TODAS las grafías del catálogo en UNA llamada, más las
    # que el job arma a mano, para ver si alguna difiere.
    from core import mercado_1816
    if not mercado_1816.disponible():
        print("  ✖ 1816 no está configurado en este entorno.\n")
        return 1
    extra = [f"{tk} @BONCAP" for tk in tickers] + [f"{tk} @TASA FIJA" for tk in tickers]
    pedir = sorted(set(candidatas) | set(extra))
    print(f"  ── PIDIENDO {len(pedir)} grafía(s) a 1816 ──\n")
    try:
        r = mercado_1816.indicadores_vigentes(pedir, campos=["tea", "tna", "spread"])
    except Exception as e:
        print(f"  ✖ 1816 no contestó: {e}\n")
        return 1
    # ⚠️ Las filas vienen bajo `instrumentos`, no en la raíz: la raíz trae la
    # metadata (`fechaOperacion`). Leer de la raíz devolvería vacío para TODAS y
    # este diag concluiría «1816 no lo tiene» sobre su propio error de lectura.
    datos = (r or {}).get("instrumentos") or {}
    print(f"     (rueda usada por 1816: {(r or {}).get('fechaOperacion')})\n")
    print(f"     {'GRAFÍA':34} {'TEA':>12} {'TNA':>12} {'MARGEN':>10}")
    for g in pedir:
        v = datos.get(g) or {}
        tea = v.get("tea")
        marca = "" if tea is not None else "   ← nada"
        print(f"     {g:34} {str(tea)[:12]:>12} {str(v.get('tna'))[:12]:>12} "
              f"{str(v.get('spread'))[:10]:>10}{marca}")
    print("\n  La que traiga TEA es la grafía buena: se agrega al job y listo.\n"
          "  Si NINGUNA trae nada, 1816 no publica esa pata y la celda vacía\n"
          "  es la verdad — ahí el camino es otro (modelarla nosotros).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
