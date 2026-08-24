"""`scripts/diag_sin_tasa.py` — POR QUÉ el motor no le calcula la TEA.

Read-only. Contesta la pregunta que deja abierta la habilidad `tasas_al_cierre`:
qué tienen en común los bonos que **operan, el motor no valúa, y 1816 tampoco
publica**. Si comparten una causa, hay UNA cosa que arreglar y no siete.

Para cada uno imprime lo que decide la valuación, en el orden en que el motor lo
pregunta (`engines/curvas.rama_calculo`):

    ejes            emisor_tipo · moneda_eje · ajuste — sin los TRES el motor
                    cae al fallback de la palabra vieja
    rama            la fórmula que le toca
    moneda_flujo    ⚠️ **el segundo vocabulario**: la rama ON despacha por acá,
                    y es un campo aparte que se carga A MANO. Mientras coincide
                    con los ejes no pasa nada; cuando diverge, el motor calcula
                    con uno y la vista clasifica con el otro
    flujos          sin cronograma no hay XIRR y no hay TEA por definición
    precio          si opera, el problema no es el mercado

    python -m scripts.diag_sin_tasa               # los que el agente tiene abiertos
    python -m scripts.diag_sin_tasa AL30 BA37     # los que le pases
"""
from __future__ import annotations

import sys

from core.postgres import get_pool


def _tickers_del_agente() -> list[str]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT sujeto FROM agente.hallazgos "
            " WHERE habilidad IN ('bono_sin_tasa', 'tasas_al_cierre') "
            "   AND estado IN ('nuevo','en_curso') ORDER BY sujeto")
        return [r[0] for r in cur.fetchall()]


def main() -> int:
    from core import curvas_ejes, curvas_sql, market_snapshot
    from engines.curvas import moneda_flujo_esperada, rama_calculo

    pedidos = [a.upper() for a in sys.argv[1:]] or _tickers_del_agente()
    if not pedidos:
        print("El agente no tiene ningún bono sin tasa abierto.")
        return 0

    docs = {(d.get("ticker_corto") or "").upper(): d
            for d in (curvas_sql.cargar_todos() or [])}
    simbolos = [(docs[t].get("ticker") or "") for t in pedidos if t in docs]
    snap = market_snapshot.cols_map([s for s in simbolos if s],
                                    ["last_price", "tea"]) or {}

    print(f"\n{len(pedidos)} bono(s) sin tasa\n")
    print(f"{'TICKER':9} {'EMISOR':12} {'MON':4} {'AJUSTE':13} {'RAMA':12} "
          f"{'MON_FLUJO':10} {'ESPERADA':9} {'FLUJOS':>6} {'PRECIO':>12}")
    causas: dict[str, list[str]] = {}
    for tk in pedidos:
        d = docs.get(tk)
        if d is None:
            causas.setdefault("no está en mercado.curvas", []).append(tk)
            print(f"{tk:9} — NO ESTÁ EN mercado.curvas")
            continue
        ejes = curvas_ejes.ejes_de_doc(d)
        rama = rama_calculo(d)
        mf = (d.get("moneda_flujo") or "").strip().upper()
        esperada = moneda_flujo_esperada(d) if rama == "on" else ""
        n_flujos = len(d.get("flujos") or [])
        m = snap.get((d.get("ticker") or "").strip()) or {}
        px = m.get("last_price")

        print(f"{tk:9} {(d.get('emisor_tipo') or '—'):12} "
              f"{(d.get('moneda_eje') or '—'):4} {(d.get('ajuste') or '—'):13} "
              f"{rama:12} {(mf or '—'):10} {(esperada or '—'):9} "
              f"{n_flujos:>6} {(float(px) if px else 0):>12,.2f}")

        # ── LA CAUSA, en el orden en que el motor decide ──────────────────
        if ejes is None:
            causas.setdefault("SIN EJES → cae al fallback de la palabra vieja",
                              []).append(tk)
        if rama == "otros":
            causas.setdefault("rama «otros» → el motor NO calcula tasa por "
                              "diseño (tamar/badlar/tpm/caución)", []).append(tk)
        if rama == "on" and esperada and mf != esperada:
            causas.setdefault(f"rama ON con `moneda_flujo` que CONTRADICE a los "
                              f"ejes → el motor despacha por el campo viejo",
                              []).append(tk)
        if rama == "on" and mf and mf not in ("USD", "DL", "ARS"):
            causas.setdefault(f"rama ON con `moneda_flujo`={mf!r}, que NO es del "
                              f"vocabulario del motor → cae al `else` y se valúa "
                              f"en pesos", []).append(tk)
        if not n_flujos and not d.get("flujo_vencimiento"):
            causas.setdefault("sin cronograma NI flujo de vencimiento → no hay "
                              "XIRR posible", []).append(tk)

    print("\n═══ LO QUE TIENEN EN COMÚN ═══\n")
    if not causas:
        print("  Ninguna causa conocida: los datos se ven bien y el motor igual "
              "no calcula.\n  Hay que mirar `engines/curvas` con uno de estos "
              "bonos a mano.")
    for causa, tks in sorted(causas.items(), key=lambda x: -len(x[1])):
        print(f"  {len(tks):>2} · {causa}")
        print(f"       {', '.join(tks)}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
