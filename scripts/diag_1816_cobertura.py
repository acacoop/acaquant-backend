"""scripts/diag_1816_cobertura.py — ¿de qué instrumentos devuelve DATOS 1816?

SIN SESGAR por nuestros bonos (pedido del user 2026-07-18): enumera TODO el
catálogo de 1816 por curva (no el cruce con mercado.curvas) y sondea con
/indicadores cuáles devuelven dato real (ultimaOperacion + precioDirty). El
reporte marca además cuáles están o no en nuestro `mercado.curvas` y en el
watch — así se ve lo que ELLOS tienen y nosotros no.

Costo: 1 crédito por curva consultada + 2 créditos por instrumento sondeado
(2 campos). Ej. soberanas+BCRA ≈ 13 créditos de curvas + ~100 instrumentos ×2
≈ ~220 créditos. Con --todas (las 33 curvas: provinciales+corporativos) puede
irse a ~500-800 instrumentos → se imprime el costo ANTES y pide confirmar con
--si (REGLA #4: medir antes de gastar).

Uso (Droplet):
    python -m scripts.diag_1816_cobertura                 # soberanas + BCRA
    python -m scripts.diag_1816_cobertura --todas --si    # las 33 curvas
    python -m scripts.diag_1816_cobertura --curva 16      # una curva puntual (id)
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from core import mercado_1816

# Soberanas + BCRA (las del discovery). --todas ignora esta lista y usa /curvas.
_CURVAS_DEFAULT = {
    1: "Soberanos ARS Badlar", 7: "Soberanos ARS CER", 8: "Soberanos USD Bonares",
    9: "Soberanos ARS tasa fija", 10: "Soberanos ARS Botes", 11: "Soberanos USD Globales",
    12: "Soberanos USD Linked", 13: "Soberanos ARS Letras CER", 14: "Soberanos Duales",
    17: "Soberanos USD Linked Lelink", 28: "Soberanos ARS Tamar", 31: "Soberanos EUR Globales",
    24: "BCRA USD",
}

_CAMPOS_SONDA = ["ultimaOperacion", "precioDirty"]
_BATCH = 50
_DIAS_FRESCO = 7  # última operación dentro de esta ventana = "opera"


def _mis_tickers() -> set[str]:
    import re

    from core import curvas_sql

    out = set()
    for d in curvas_sql.cargar_todos():
        tc = (d.get("ticker_corto") or "").strip().upper()
        if tc:
            m = re.match(r"^([A-Z]+\d+)[DC]$", tc)
            out.add(m.group(1) if m else tc)
    return out


def _watch_tickers() -> set[str]:
    from core.postgres import get_pool

    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker FROM research.mkt_1816_watch WHERE activo")
            return {r[0] for r in cur.fetchall()}
    except Exception:
        return set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--todas", action="store_true",
                    help="TODAS las curvas de 1816 (provinciales + corporativos incluidos)")
    ap.add_argument("--curva", type=int, help="una curva puntual (id de 1816)")
    ap.add_argument("--si", action="store_true",
                    help="confirmar el gasto cuando el sondeo es grande (--todas)")
    args = ap.parse_args()

    if not mercado_1816.disponible():
        print("✗ falta MERCADO_1816_API_KEY")
        return

    # 1) curvas a explorar
    if args.curva:
        curvas = {args.curva: f"curva {args.curva}"}
    elif args.todas:
        curvas = {c["id"]: c["name"] for c in mercado_1816.curvas()}
    else:
        curvas = _CURVAS_DEFAULT
    print(f"Curvas a explorar: {len(curvas)}")

    # 2) TODO el catálogo de esas curvas (sin cruzar con lo nuestro)
    insts: dict[str, dict] = {}
    for cid, nombre in curvas.items():
        try:
            for i in mercado_1816.instrumentos(curva_id=cid) or []:
                tk = (i.get("ticker") or "").strip().upper()
                if tk and tk not in insts:
                    insts[tk] = {"curva": i.get("curva") or nombre,
                                 "vto": i.get("fechaVencimiento")}
        except Exception as e:
            print(f"  ⚠ curva {cid} ({nombre}): {e}")
    tickers = sorted(insts)
    costo = len(tickers) * len(_CAMPOS_SONDA)
    print(f"Instrumentos en 1816: {len(tickers)} → sondeo ≈ {costo} créditos")
    if costo > 400 and not args.si:
        print("✗ Sondeo grande — re-corré con --si para confirmar el gasto.")
        return

    # 3) sondeo por lotes: ¿devuelve dato? ¿fresco?
    corte_fresco = datetime.now(UTC) - timedelta(days=_DIAS_FRESCO)
    resultado: dict[str, dict] = {}
    for i in range(0, len(tickers), _BATCH):
        lote = tickers[i:i + _BATCH]
        try:
            d = mercado_1816.indicadores(lote, _CAMPOS_SONDA)
        except Exception as e:
            print(f"  ⚠ lote {i // _BATCH + 1}: {e}")
            continue
        datos = d.get("instrumentos") or {}
        for tk in lote:
            v = datos.get(tk) or {}
            ult = v.get("ultimaOperacion")
            fresco = False
            if ult:
                try:
                    fresco = datetime.fromisoformat(str(ult).replace("Z", "+00:00")) >= corte_fresco
                except ValueError:
                    pass
            resultado[tk] = {"precio": v.get("precioDirty"), "ult": ult, "fresco": fresco}

    # 4) reporte, cruzado con lo nuestro (informativo, no filtra)
    mios = _mis_tickers()
    watch = _watch_tickers()
    con_dato = [t for t in tickers if resultado.get(t, {}).get("precio") is not None]
    frescos = [t for t in con_dato if resultado[t]["fresco"]]
    sin_dato = [t for t in tickers if t not in con_dato]

    print(f"\n{'=' * 78}")
    print(f"CON DATO: {len(con_dato)} (frescos ≤{_DIAS_FRESCO}d: {len(frescos)}) · "
          f"SIN DATO: {len(sin_dato)}")
    print(f"{'=' * 78}")
    print(f"{'ticker':10} {'curva':28} {'últ.operación':20} {'fresco':6} {'mío':4} {'watch'}")
    for t in tickers:
        r = resultado.get(t, {})
        marca_f = "✓" if r.get("fresco") else ("·" if r.get("precio") is not None else "✗")
        print(f"{t:10} {insts[t]['curva'][:28]:28} {str(r.get('ult') or '—')[:19]:20} "
              f"{marca_f:6} {'sí' if t in mios else '—':4} {'sí' if t in watch else '—'}")

    ellos_no_yo = [t for t in frescos if t not in mios]
    if ellos_no_yo:
        print(f"\n💡 OPERAN en 1816 y NO están en mi Curvas ({len(ellos_no_yo)}): "
              + ", ".join(ellos_no_yo))
    fuera_watch = [t for t in frescos if t not in watch]
    if fuera_watch:
        print(f"💡 Frescos FUERA del watch de series ({len(fuera_watch)}): "
              + ", ".join(fuera_watch))
    print("\nPara sumar alguno al watch de series: INSERT en research.mkt_1816_watch "
          "(o pedímelo y lo agrego al discovery).")


if __name__ == "__main__":
    main()
