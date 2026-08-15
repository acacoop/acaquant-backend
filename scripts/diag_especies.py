"""scripts/diag_especies.py — TICKER vs ESPECIE en el master de renta fija.

Insumo para modelar bien el activo financiero (docs/RENTA_FIJA.md §0). READ-ONLY.

**El problema, en una línea**: `mercado.curvas.ticker_corto` mezcla DOS cosas —
el ticker del bono (`AL30`) y la ESPECIE en la que se lo mira (`AL30` pesos,
`AL30D` dólar MEP, `AL30C` cable). Son el MISMO bono con el mismo cuadro de
flujos, cotizando en tres monedas distintas.

Las consecuencias que ya se ven en la vista:

  · La tabla muestra `AL30D` como si fuera el ticker, cuando el ticker es `AL30`.
  · Si el master eligió la especie equivocada, el precio es de OTRO instrumento:
    `AO29` (pesos) marca ~141.430 al lado de bonos que cotizan ~90, porque los
    hard dollar en pesos van por otra escala. La curva se dibuja con ese número.
  · Un bono existe UNA vez en el master, así que no se puede ofrecer "verlo en
    pesos / MEP / cable": la especie quedó congelada en el alta.

**Lo que este diag NO hace**: proponer el modelo. Primero hay que ver, para cada
bono, qué especies existen realmente en el mercado, cuál eligió el master y si
ese precio es del orden esperado. Con eso se decide.

Uso:
    python -m scripts.diag_especies                # el relevamiento completo
    python -m scripts.diag_especies --curva soberanos
    python -m scripts.diag_especies --sospechosos  # solo los que pintan mal
"""
from __future__ import annotations

import argparse
import re

from core.postgres import get_pool

# Especie al final del ticker corto: D = MEP (dólar local), C = cable.
_RE_ESPECIE = re.compile(r"^([A-Z]+\d+)([DC])$")

_ESPECIES = {"": "PESOS", "D": "MEP", "C": "CABLE"}


def _partes(ticker_completo: str) -> tuple[list[str], int] | None:
    """'MERV - XMEV - AL30D - 24hs' → (segmentos, índice del que es el ticker).

    Por SPLIT y no por regex: un `^(.*?-\s*)([A-Z0-9]+)(\s*-.*)$` parece que
    aísla el ticker y en realidad captura `XMEV` (el mercado), porque el
    no-greedy corta en el primer guión. Lo destapó el smoke con datos falsos —
    de otro modo el diag habría reportado "ninguna especie" para TODO y el
    número habría pasado por bueno.
    """
    segs = [x.strip() for x in (ticker_completo or "").split(" - ")]
    return (segs, 2) if len(segs) >= 3 else None


def _base_y_especie(tc: str) -> tuple[str, str]:
    """`AL30D` → ('AL30', 'D'). `TX26` → ('TX26', '')."""
    m = _RE_ESPECIE.match((tc or "").strip().upper())
    return (m.group(1), m.group(2)) if m else ((tc or "").strip().upper(), "")


def _master(curva: str | None) -> list[dict]:
    sql = ("SELECT ticker_corto, ticker, curva, tipo, emisor_tipo, moneda_eje, "
           "ajuste, ley FROM mercado.curvas")
    params: tuple = ()
    if curva:
        sql += " WHERE curva = %s"
        params = (curva,)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _snapshot() -> dict[str, dict]:
    """{ticker_completo: {last_price, tea, ...}} de TODO el snapshot — así se ve
    qué especies existen de verdad en el mercado, no las que suponemos."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, last_price, tea, duration, total_nominals "
                    "FROM mercado.market_snapshot")
        cols = [d[0] for d in cur.description]
        return {r[0]: dict(zip(cols, r, strict=False)) for r in cur.fetchall()}


def main() -> None:
    ap = argparse.ArgumentParser(description="Ticker vs especie en mercado.curvas")
    ap.add_argument("--curva", help="acotar a una curva del master")
    ap.add_argument("--sospechosos", action="store_true",
                    help="solo los bonos cuyo precio pinta de otra especie")
    args = ap.parse_args()

    master = _master(args.curva)
    snap = _snapshot()
    print("=" * 104)
    print("TICKER vs ESPECIE — cómo está modelado hoy el activo en mercado.curvas")
    print("=" * 104)
    print(f"instrumentos en el master: {len(master)} · filas en market_snapshot: {len(snap)}")

    # 1) ¿cuántos ticker_corto traen la especie pegada?
    con_especie: dict[str, int] = {}
    for d in master:
        _, e = _base_y_especie(d["ticker_corto"])
        con_especie[e] = con_especie.get(e, 0) + 1
    print("\n1) EL TICKER_CORTO, ¿trae la especie pegada?")
    for e, n in sorted(con_especie.items(), key=lambda x: -x[1]):
        print(f"     {_ESPECIES.get(e, e):<6} ({e or 'sin sufijo'}): {n}")
    print("   Un ticker con sufijo NO es un ticker: es el bono + la especie en la")
    print("   que se lo mira. El bono es el mismo y el cuadro de flujos también.")

    # 2) por bono: qué especies EXISTEN en el mercado y cuál usa el master
    filas = []
    for d in master:
        tc = (d["ticker_corto"] or "").strip().upper()
        base, esp = _base_y_especie(tc)
        p = _partes(d["ticker"] or "")
        disponibles: dict[str, dict] = {}
        if p:
            segs, i = p
            for suf, nombre in _ESPECIES.items():
                cand = " - ".join(segs[:i] + [base + suf] + segs[i + 1:])
                if cand in snap:
                    disponibles[nombre] = snap[cand]
        elegido = _ESPECIES.get(esp, esp)
        px = {k: v.get("last_price") for k, v in disponibles.items()}
        px_elegido = px.get(elegido)
        # Sospecha: el precio del elegido está a más de 100x de alguna hermana.
        # Un hard dollar en pesos cotiza ~1.400x el mismo bono en dólares, así
        # que la diferencia de ORDEN delata la especie cambiada. No se corrige
        # nada acá: se reporta para mirarlo.
        otros = [v for k, v in px.items() if k != elegido and v]
        sospechoso = bool(px_elegido and otros and
                          max(max(otros) / px_elegido, px_elegido / min(otros)) > 100)
        filas.append({**d, "base": base, "especie": elegido,
                      "disponibles": px, "px": px_elegido, "sospechoso": sospechoso})

    mostrar = [f for f in filas if f["sospechoso"]] if args.sospechosos else filas
    print(f"\n2) POR BONO — qué especies existen y cuál usa el master ({len(mostrar)})")
    print(f"\n{'BASE':<8}{'USA':<7}{'PRECIO':>13}   ESPECIES EN EL SNAPSHOT (precio)")
    print("─" * 104)
    for f in sorted(mostrar, key=lambda x: (not x["sospechoso"], x["base"])):
        disp = " · ".join(
            f"{k}={v:,.2f}" if isinstance(v, (int, float)) else f"{k}=--"
            for k, v in sorted(f["disponibles"].items())) or "(ninguna en snapshot)"
        marca = " ⚠" if f["sospechoso"] else ""
        px = f"{f['px']:,.2f}" if isinstance(f["px"], (int, float)) else "--"
        print(f"{f['base'][:8]:<8}{f['especie']:<7}{px:>13}   {disp}{marca}")
    print("─" * 104)

    sos = [f for f in filas if f["sospechoso"]]
    multi = [f for f in filas if len(f["disponibles"]) > 1]
    sin_snap = [f for f in filas if not f["disponibles"]]
    print("\n3) RESUMEN")
    print(f"   Bonos con MÁS DE UNA especie cotizando: {len(multi)} de {len(filas)}")
    print("     → hoy el master elige UNA y la congela; no se puede ver el mismo")
    print("       bono en pesos/MEP/cable sin dar de alta otro instrumento.")
    print(f"   ⚠ Con precio de otro ORDEN que sus hermanas: {len(sos)}")
    for f in sos[:20]:
        print(f"       {f['base']:<8} usa {f['especie']:<6} "
              + " · ".join(f"{k}={v:,.2f}" for k, v in sorted(f["disponibles"].items())
                           if isinstance(v, (int, float))))
    print(f"   Sin ninguna especie en el snapshot: {len(sin_snap)}")
    if sin_snap:
        print("       " + ", ".join(f["base"] for f in sin_snap[:25]))
    print("\n   (Nada de esto se corrige acá: es el relevamiento para decidir el modelo.)")


if __name__ == "__main__":
    main()
