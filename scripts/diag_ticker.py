"""scripts/diag_ticker.py — POR QUÉ este bono no tiene precio. READ-ONLY.

Traza la cadena COMPLETA de un ticker, de punta a punta, en un solo comando.
Cuando un papel aparece en blanco en la vista, la causa está en UNO de estos
seis eslabones y hasta ahora había que mirarlos de a uno en seis lugares:

    mercado.curvas        →  ¿qué símbolo usa el master para pedir el precio?
    mercado.especies      →  ¿qué patas tiene, cuál es la default?
    portafolio.assets     →  ¿qué símbolo suscribe el motor de portfolio?
    catálogo de Primary   →  ¿ese símbolo EXISTE? (si no, el WS lo descarta)
    market_snapshot       →  ¿llegó precio a la tabla que lee la vista?
    portfolio_snapshot    →  ¿llegó precio a la tabla que lee el PnL?

La respuesta sale de comparar los seis: el primero que corta la cadena es la
causa. Todo lo de abajo de ese punto está vacío como consecuencia, no como
problema propio.

Uso:
    python -m scripts.diag_ticker AO29
    python -m scripts.diag_ticker AO29 AL30 GD29     # varios de una
"""
from __future__ import annotations

import sys

from core.instrumentos_validos import validos
from core.postgres import get_pool

_SEP = "─" * 88


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _v(x) -> str:
    return "—" if x is None or str(x).strip() == "" else str(x)


def trazar(tk: str, univ: set[str] | None) -> None:
    tk = tk.strip().upper()
    print(f"\n{'=' * 88}\n{tk}\n{'=' * 88}")

    # 1) el master
    cur = _q("SELECT ticker, instrumento, emisor, emisor_tipo, moneda_eje, ajuste, "
             "curva, fecha_vencimiento, "
             "(SELECT count(*) FROM jsonb_array_elements(flujos)) AS n_flujos "
             "FROM mercado.curvas WHERE upper(ticker) = %s", (tk,))
    print("\n  1· mercado.curvas (el master)")
    if not cur:
        print(f"     ✗ NO existe fila con ticker={tk!r} — el bono no está en el master.")
        print("       Todo lo de abajo va a estar vacío por eso.")
    else:
        c = cur[0]
        print(f"     símbolo que usa : {_v(c['instrumento'])}")
        print(f"     emisor          : {_v(c['emisor'])}   ({_v(c['emisor_tipo'])})")
        print(f"     moneda / ajuste : {_v(c['moneda_eje'])} / {_v(c['ajuste'])}"
              f"   curva={_v(c['curva'])}")
        print(f"     vencimiento     : {_v(c['fecha_vencimiento'])}   "
              f"flujos cargados: {c['n_flujos']}")

    # 2) sus patas
    esp = _q("SELECT simbolo, ticker_especie, especie, plazo, es_default, validado "
             "FROM mercado.especies WHERE upper(ticker) = %s "
             "ORDER BY es_default DESC, especie, plazo", (tk,))
    print(f"\n  2· mercado.especies — {len(esp)} pata(s)")
    for e in esp:
        marca = "★" if e["es_default"] else " "
        val = "válido" if e["validado"] else ("INVÁLIDO" if e["validado"] is False else "?")
        print(f"     {marca} {_v(e['simbolo']):<34} {_v(e['especie']):<7}"
              f"{_v(e['plazo']):<6} {val}")
    if not esp:
        print("     ✗ ninguna. `sembrar_especies` no le encontró patas en Primary.")

    # 3) el catálogo
    ass = _q("SELECT unidad, instrumento, instrumento_usd, vigente, vigencia_motivo "
             "FROM portafolio.assets WHERE upper(ticker) = %s", (tk,))
    print(f"\n  3· portafolio.assets — {len(ass)} unidad(es)")
    for a in ass:
        vig = "vigente" if a["vigente"] is not False else f"BAJA ({_v(a['vigencia_motivo'])})"
        print(f"     {_v(a['unidad'])[:44]:<46} {vig}")
        print(f"        suscribe ARS : {_v(a['instrumento'])}")
        print(f"        suscribe USD : {_v(a['instrumento_usd'])}")

    # 4) ¿existen esos símbolos en Primary?
    simbolos = {c.get("instrumento") for c in cur if c.get("instrumento")}
    simbolos |= {a[k] for a in ass for k in ("instrumento", "instrumento_usd") if a.get(k)}
    simbolos |= {e["simbolo"] for e in esp if e.get("simbolo")}
    print("\n  4· ¿existen en el catálogo de Primary?")
    if univ is None:
        print("     (catálogo no disponible — el filtro del WS no está descartando nada)")
    else:
        for s in sorted(simbolos):
            print(f"     {'✅' if s in univ else '✗ NO EXISTE → el WS lo descarta'}  {s}")

    # 5 y 6) ¿llegó precio?
    for tabla, quien in (("mercado.market_snapshot", "la vista de renta fija"),
                         ("valuaciones.portfolio_snapshot", "el PnL / portfolios")):
        print(f"\n  {'5' if 'market' in tabla else '6'}· {tabla} → lo lee {quien}")
        if not simbolos:
            print("     (sin símbolos que buscar)")
            continue
        filas = _q(f"SELECT ticker, last_price, updated_at FROM {tabla} "
                   f"WHERE ticker = ANY(%s) ORDER BY ticker", (sorted(simbolos),))
        if not filas:
            print("     ✗ SIN precio para ninguno de sus símbolos.")
        for f in filas:
            print(f"     {_v(f['ticker']):<34} last={_v(f['last_price']):<14}"
                  f"{_v(f['updated_at'])}")


def main() -> None:
    tickers = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not tickers:
        print("Uso: python -m scripts.diag_ticker <TICKER> [TICKER…]")
        return
    univ = validos(forzar=True)
    print(f"catálogo de Primary: {len(univ) if univ is not None else 'NO DISPONIBLE'}")
    for tk in tickers:
        trazar(tk, univ)
    print(f"\n{_SEP}\n  Cómo se lee: el PRIMER eslabón que corta es la causa. Lo de abajo")
    print("  está vacío como CONSECUENCIA, no como problema propio.")
    print(f"{_SEP}")


if __name__ == "__main__":
    main()
