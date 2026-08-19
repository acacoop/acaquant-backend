"""scripts/diag_blob_vs_columna.py — ¿EL MOTOR Y LA VISTA HABLAN DEL MISMO SÍMBOLO?

**Read-only.**

El AO29 tiene precio y métricas sanas en `market_snapshot` y la fila de la app
sale igual toda en «--». Las dos cosas pueden ser ciertas a la vez si **el motor
escribe con un nombre y la vista busca con otro**:

    MOTOR   `engines/curvas.py` → `instrumento.get("ticker")`   ← el BLOB `data`
    VISTA   `LEFT JOIN market_snapshot s ON s.ticker = c.instrumento`  ← la COLUMNA

Y el renombre del 2026-08-15 migró las COLUMNAS pero **dejó el blob intacto a
propósito** (lo leen ~500 lugares). O sea que hoy conviven dos nombres para lo
mismo, con el significado INVERTIDO entre uno y otro:

    COLUMNA  ticker = «AL30»                 instrumento = «MERV - XMEV - AL30 - 24hs»
    BLOB     ticker = «MERV - XMEV - …»      ticker_corto = «AL30»

Mientras los dos digan lo mismo no pasa nada. Este script mide si es así — y si
no, muestra **cuál de los dos tiene el precio**, que es lo que decide si la fila
se ve o no.

    python -m scripts.diag_blob_vs_columna
"""
from __future__ import annotations

import sys

from core.postgres import get_pool


def main() -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.ticker              AS col_corto,
                   c.instrumento         AS col_simbolo,
                   c.data->>'ticker_corto' AS blob_corto,
                   c.data->>'ticker'       AS blob_simbolo,
                   sc.last_price         AS px_por_columna,
                   sb.last_price         AS px_por_blob
            FROM mercado.curvas c
            LEFT JOIN mercado.market_snapshot sc ON sc.ticker = c.instrumento
            LEFT JOIN mercado.market_snapshot sb ON sb.ticker = c.data->>'ticker'
            ORDER BY c.ticker
        """)
        filas = [dict(zip([d[0] for d in cur.description], r, strict=True))
                 for r in cur.fetchall()]

    difieren = [f for f in filas
                if (f["col_simbolo"] or "") != (f["blob_simbolo"] or "")]
    # El caso que ROMPE LA PANTALLA: el blob tiene precio y la columna no, así
    # que el motor lo está priceando y la vista lo busca donde no está.
    rotos = [f for f in difieren
             if f["px_por_blob"] is not None and f["px_por_columna"] is None]
    # El inverso: la columna tiene precio y el blob no. La vista se ve bien y el
    # AGENTE es el que mira al lado equivocado.
    ciegos = [f for f in difieren
              if f["px_por_columna"] is not None and f["px_por_blob"] is None]

    print(f"\n{len(filas)} bonos en `mercado.curvas`\n" + "=" * 70)
    print(f"  el SÍMBOLO del blob y el de la columna difieren : {len(difieren)}")
    print(f"    → de esos, el precio está SOLO en el blob     : {len(rotos)}"
          "   ← la fila sale en «--»")
    print(f"    → de esos, el precio está SOLO en la columna  : {len(ciegos)}"
          "   ← el AGENTE mira al lado equivocado")

    for titulo, grupo in (("ROMPEN LA PANTALLA", rotos),
                          ("CIEGOS PARA EL AGENTE", ciegos),
                          ("DIFIEREN SIN CONSECUENCIA VISIBLE",
                           [f for f in difieren if f not in rotos and f not in ciegos])):
        if not grupo:
            continue
        print(f"\n{titulo} ({len(grupo)})\n" + "-" * 70)
        for f in grupo[:40]:
            print(f"  {f['col_corto'] or f['blob_corto']}")
            print(f"      columna : {f['col_simbolo']!s:46} px={f['px_por_columna']}")
            print(f"      blob    : {f['blob_simbolo']!s:46} px={f['px_por_blob']}")

    # Y el corto, que es la OTRA mitad del renombre.
    corto_dif = [f for f in filas
                 if (f["col_corto"] or "") != (f["blob_corto"] or "")]
    print(f"\n  el TICKER CORTO difiere entre blob y columna : {len(corto_dif)}")
    for f in corto_dif[:20]:
        print(f"      columna={f['col_corto']!r}  blob={f['blob_corto']!r}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
