"""scripts/sembrar_especies.py — PASO 8 del rediseño de renta fija: las PATAS.

Doc madre: `docs/RENTA_FIJA.md` §0. Puebla `mercado.especies` con TODAS las
especies que cada bono del master tiene en Primary (`manager.pyrofex_instruments`),
en vez del único casillero que hoy tiene `mercado.curvas.instrumento`.

**Es invisible para la vista**: nadie lee `mercado.especies` todavía. Se siembra
primero, se mira, y recién después los readers se mudan.

**DRY-RUN por default** (REGLA #4). Idempotente: `simbolo` es la PK y el upsert
no pisa `activa` ni `es_default` calculados de nuevo — los recalcula igual, que es
lo correcto porque salen del master.

Lo que hace, en orden:

  1. Lee el master (`mercado.curvas`) y el universo de Primary.
  2. Por cada bono arma sus patas: base del ticker → todos los símbolos de Primary.
  3. Marca `es_default` la que el master está usando HOY (`curvas.instrumento`).
  4. **Avisa de las cruzadas**: bonos cuyo default NO es de su moneda. Esa es la
     lista de precios de otra escala — medido el 2026-08-15: `CO32` (denominado
     en USD y apuntando a la especie en PESOS).

Uso:
    python -m scripts.sembrar_especies              # DRY-RUN (default)
    python -m scripts.sembrar_especies --aplicar    # escribe mercado.especies
"""
from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime

from core.postgres import get_pool

_RE_ESPECIE = re.compile(r"^([A-Z]+\d+)([DC])$")
# El sufijo del SÍMBOLO manda sobre el label: `engines/curvas.py:223` ya decide la
# moneda así ("el ticker_corto es un label humano y puede no reflejar la moneda").
_ESPECIE = {"": ("pesos", "ARS"), "D": ("mep", "USD"), "C": ("cable", "USD")}
_ESPERADA = {"USD": {"mep", "cable"}, "EUR": {"mep", "cable"}, "ARS": {"pesos"}}


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _base(tk: str) -> tuple[str, str]:
    m = _RE_ESPECIE.match((tk or "").strip().upper())
    return (m.group(1), m.group(2)) if m else ((tk or "").strip().upper(), "")


def _segs(simbolo: str) -> list[str] | None:
    s = [x.strip() for x in (simbolo or "").split(" - ")]
    return s if len(s) >= 4 else None


def _universo() -> dict[str, list[dict]]:
    """{BASE: [pata]} desde Primary. Es la fuente que NO depende de lo que el
    master eligió — a diferencia de `market_snapshot`, que solo tiene lo que el
    motor suscribe y el motor suscribe desde el master (circular)."""
    filas = _q("SELECT DISTINCT i->>'ticker' AS simbolo "
               "FROM manager.pyrofex_instruments p, jsonb_array_elements(p.instruments) i "
               "WHERE i->>'ticker' IS NOT NULL")
    out: dict[str, list[dict]] = {}
    for f in filas:
        s = _segs(f["simbolo"])
        if not s:
            continue
        base, suf = _base(s[2])
        esp, mon = _ESPECIE.get(suf, (None, None))
        if not esp:
            continue
        out.setdefault(base, []).append({
            "simbolo": f["simbolo"], "ticker": base, "ticker_especie": s[2].upper(),
            "especie": esp, "moneda": mon, "plazo": s[3]})
    return out


def _armar(curvas: list[dict], univ: dict[str, list[dict]]) -> tuple[list[dict], list[dict], list[str]]:
    """(filas a escribir, cruzadas, sin patas). PURO — testeable sin base."""
    filas: list[dict] = []
    cruzadas: list[dict] = []
    sin: list[str] = []
    for c in curvas:
        tk = (c.get("ticker") or "").strip().upper()
        base, _ = _base(tk)
        patas = univ.get(base) or []
        if not patas:
            sin.append(tk)
            continue
        actual = (c.get("instrumento") or "").strip()
        default = next((p for p in patas if p["simbolo"] == actual), None)
        for p in patas:
            filas.append({**p, "es_default": p["simbolo"] == actual})
        # ¿El default apunta a una especie que NO es la de su moneda? Ahí el precio
        # que muestra la vista es de otra escala (un hard dollar en pesos cotiza
        # ~1.400x). Solo se reporta: corregirlo es cambiar el instrumento del master.
        esperada = _ESPERADA.get((c.get("moneda_eje") or "").upper())
        if default and esperada and default["especie"] not in esperada:
            alt = [p for p in patas if p["especie"] in esperada]
            cruzadas.append({"ticker": tk, "curva": c.get("curva"),
                             "moneda": c.get("moneda_eje"), "usa": default["especie"],
                             "deberia": sorted({p["especie"] for p in alt}),
                             "simbolo_ok": alt[0]["simbolo"] if alt else None})
        elif not default:
            cruzadas.append({"ticker": tk, "curva": c.get("curva"),
                             "moneda": c.get("moneda_eje"), "usa": "(el símbolo del "
                             "master no está en Primary)", "deberia": [], "simbolo_ok": None})
    return filas, cruzadas, sin


def _reporte(filas: list[dict], cruzadas: list[dict], sin: list[str],
             curvas: list[dict]) -> None:
    por_ticker: dict[str, int] = {}
    for f in filas:
        por_ticker[f["ticker"]] = por_ticker.get(f["ticker"], 0) + 1
    reparto: dict[int, int] = {}
    for n in por_ticker.values():
        reparto[n] = reparto.get(n, 0) + 1

    print(f"\n{'=' * 96}\nPATAS ENCONTRADAS\n{'=' * 96}")
    print(f"  bonos del master: {len(curvas)} · con patas en Primary: {len(por_ticker)} "
          f"· sin ninguna: {len(sin)}")
    print(f"  filas a escribir en mercado.especies: {len(filas)}")
    print(f"  bonos por CANTIDAD de patas: {dict(sorted(reparto.items()))}")
    sin_def = sorted({f["ticker"] for f in filas} -
                     {f["ticker"] for f in filas if f["es_default"]})
    print(f"  bonos sin ninguna pata marcada `es_default`: {len(sin_def)}")
    if sin_def:
        print(f"    {', '.join(sin_def[:20])}")
        print("    → su `curvas.instrumento` no coincide con ningún símbolo de Primary.")

    multi = sorted(t for t, n in por_ticker.items() if n > 1)
    if multi:
        print(f"\n  MULTI-PATA ({len(multi)}) — los que hoy pierden especies:")
        for t in multi[:25]:
            ps = sorted(f'{f["especie"]}/{f["plazo"]}' for f in filas if f["ticker"] == t)
            d = next((f["ticker_especie"] for f in filas
                      if f["ticker"] == t and f["es_default"]), "—")
            print(f"    {t:<8} default={d:<8} {' · '.join(ps)}")
        if len(multi) > 25:
            print(f"    … y {len(multi) - 25} más")

    print(f"\n{'=' * 96}\n⚠ DEFAULT CRUZADO — el precio que se muestra es de otra escala "
          f"({len(cruzadas)})\n{'=' * 96}")
    if not cruzadas:
        print("  ninguno.")
    for c in cruzadas:
        cur, mon = c["curva"] or "", c["moneda"] or ""
        print(f"  {c['ticker']:<8} curva={cur:<14} moneda={mon:<5} usa={c['usa']}")
        if c["simbolo_ok"]:
            print(f"           debería usar ({'/'.join(c['deberia'])}): {c['simbolo_ok']}")
    if sin:
        print(f"\n  SIN NINGUNA PATA en Primary ({len(sin)}): {', '.join(sorted(sin)[:30])}")
        print("    → o el discovery de Primary está viejo, o el bono no cotiza más.")


def _escribir(filas: list[dict]) -> int:
    ahora = datetime.now(UTC)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO mercado.especies (simbolo, ticker, ticker_especie, especie, "
            "moneda, plazo, es_default, activa, actualizado_at) "
            "VALUES (%(simbolo)s, %(ticker)s, %(ticker_especie)s, %(especie)s, "
            "%(moneda)s, %(plazo)s, %(es_default)s, true, %(ts)s) "
            "ON CONFLICT (simbolo) DO UPDATE SET ticker = EXCLUDED.ticker, "
            "ticker_especie = EXCLUDED.ticker_especie, especie = EXCLUDED.especie, "
            "moneda = EXCLUDED.moneda, plazo = EXCLUDED.plazo, "
            "es_default = EXCLUDED.es_default, actualizado_at = EXCLUDED.actualizado_at",
            [{**f, "ts": ahora} for f in filas])
        return cur.rowcount or len(filas)


def main() -> None:
    ap = argparse.ArgumentParser(description="Paso 8: sembrar mercado.especies")
    ap.add_argument("--aplicar", action="store_true", help="escribe (default: dry-run)")
    args = ap.parse_args()

    print("=" * 96)
    print("PASO 8 — LAS PATAS DE CADA BONO (mercado.especies)")
    print("=" * 96)

    curvas = _q("SELECT ticker, instrumento, curva, moneda_eje FROM mercado.curvas")
    if not curvas:
        print("✗ mercado.curvas vino vacío")
        return
    univ = _universo()
    if not univ:
        print("✗ manager.pyrofex_instruments vino vacío — corré el discovery primero")
        return
    print(f"universo de Primary: {len(univ)} tickers base")

    filas, cruzadas, sin = _armar(curvas, univ)
    _reporte(filas, cruzadas, sin, curvas)

    if not args.aplicar:
        print(f"\n[DRY-RUN] no se escribió nada. Con --aplicar entran {len(filas)} filas.")
        return
    faltan = not _q("SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'mercado' AND table_name = 'especies'")
    if faltan:
        print("\n🛑 NO se escribió nada: falta la tabla mercado.especies.")
        print("   Corré primero:  python -m scripts.apply_schema")
        return
    n = _escribir(filas)
    print(f"\n✅ {n} filas en mercado.especies. La vista NO cambia: nadie la lee todavía.")


if __name__ == "__main__":
    main()
