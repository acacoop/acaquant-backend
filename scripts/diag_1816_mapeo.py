"""scripts/diag_1816_mapeo.py — el insumo de diseño para AUTOMATIZAR el master de
renta fija con 1816. Doc madre: docs/VISTA_RESEARCH.md §4.10.

El objetivo del user (2026-08-15) NO es research: es que `mercado.curvas` deje de
mantenerse a mano — que un bono nuevo de una licitación aparezca solo, con su
cuadro de flujos, y que las curvas propias hablen el mismo idioma que las de
1816 para que eso se pueda automatizar. Precios/TEA/TNA los sigue calculando el
sistema: acá NO se piden `/indicadores` ni `/series`, así que el gasto recurrente
es despreciable.

Contesta las TRES preguntas que hay que responder antes de codear el job:

  1. **¿Cómo mapean MIS curvas contra las de 1816?** Para cada valor de
     `mercado.curvas.curva` muestra en qué curvas de 1816 caen sus tickers, con
     los números. El mapeo se DERIVA de dónde cayeron los bonos, no se inventa:
     si mi `tasa_fija` se parte entre dos curvas de 1816, eso se ve acá.
  2. **¿Mis flujos coinciden con los de 1816, familia por familia?** Toma una
     muestra de CADA una de mis curvas, pide el cashflow y compara cupón a cupón
     (cantidad, fechas, amortización, interés) mostrando el primer cupón crudo de
     los dos lados. Es para VER los ejemplos y decidir el adaptador, no para
     escribir nada.
  3. **¿Qué apareció en 1816 que yo no tengo?** Los instrumentos de 1816 fuera de
     `mercado.curvas`, agrupados por curva y ordenados por fecha de emisión —
     el prototipo de "qué detectaría el job diario".

READ-ONLY. No escribe una sola fila.

⚠ CRÉDITOS: el censo son ~29 (o 0 con --desde, reusando el del otro diag) y la
comparación de flujos ~14 por ticker probado (1 por cupón). El bloque 3 es GRATIS
(sale del censo). El script mide el balance antes y después de cada bloque.

Uso:
    python -m scripts.diag_1816_mapeo                        # los tres bloques
    python -m scripts.diag_1816_mapeo --desde /tmp/1816.json # reusa censo (ahorra 29)
    python -m scripts.diag_1816_mapeo --mapeo                # SOLO el bloque 1 (curvas)
    python -m scripts.diag_1816_mapeo --novedades            # SOLO el bloque 3 (gratis)
    python -m scripts.diag_1816_mapeo --por-familia 3        # muestra de flujos más grande
    python -m scripts.diag_1816_mapeo --curva cer            # comparar SOLO esa curva mía
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from core import mercado_1816
from scripts.diag_1816_cashflow import (
    _delta,
    _fecha,
    _norm,
    _num,
    _saldo,
    censar,
)

_TOL = 0.01          # misma tolerancia que el otro diag (nuestro master redondea)
_DIAS_NOVEDAD = 180  # ventana por default de "esto es nuevo"


# ── lectura de MI master ─────────────────────────────────────────────────────


def _mi_master() -> list[dict]:
    """Docs completos de `mercado.curvas` (con `flujos`). [] si no hay base."""
    try:
        from core import curvas_sql
        return curvas_sql.cargar_todos()
    except Exception as e:
        print(f"⚠ no se pudo leer mercado.curvas ({str(e)[:80]}) — sin master, "
              "los bloques 1 y 2 no corren.")
        return []


# ── 1) mapeo de curvas: las mías → las de 1816 ───────────────────────────────


def _mapeo(master: list[dict], univ: dict) -> None:
    """Para cada curva MÍA, dónde caen sus tickers en el catálogo de 1816.

    Esto es lo que decide si el job diario puede ser automático: si mi `cer`
    equivale a UNA curva de 1816, "apareció algo nuevo en esa curva" es una
    pregunta que la máquina puede contestar sola. Si mi curva se parte en varias,
    hay que decidir a mano cuál miro (o partir la mía)."""
    porcurva: dict[str, dict] = {}
    for d in master:
        mi = (d.get("curva") or "(sin curva)").strip()
        tk = _norm(d.get("ticker_corto"))
        e = porcurva.setdefault(mi, {"n": 0, "en1816": 0, "destinos": {}, "faltan": []})
        e["n"] += 1
        inst = univ.get(tk)
        if inst:
            e["en1816"] += 1
            c = inst.get("_curva") or "?"
            e["destinos"][c] = e["destinos"].get(c, 0) + 1
        else:
            e["faltan"].append(tk)

    print(f"\n{'=' * 100}\n1) MAPEO DE CURVAS — mis familias contra las de 1816\n{'=' * 100}")
    print(f"{'MI CURVA':<20}{'N':>4}{'EN 1816':>9}  CURVA(S) DE 1816")
    print("─" * 100)
    limpio, partido = [], []
    for mi, e in sorted(porcurva.items(), key=lambda x: -x[1]["n"]):
        dest = sorted(e["destinos"].items(), key=lambda x: -x[1])
        txt = " · ".join(f"{c} ({n})" for c, n in dest[:3])
        if len(dest) > 3:
            txt += f" · +{len(dest) - 3} más"
        print(f"{mi[:20]:<20}{e['n']:>4}{e['en1816']:>9}  {txt or '—'}")
        if not dest:
            continue
        # "limpio" = una sola curva de 1816 se lleva >=80% de mis tickers → el job
        # puede vigilar ESA curva sin ambigüedad.
        if dest[0][1] / max(e["en1816"], 1) >= 0.8:
            limpio.append((mi, dest[0][0], dest[0][1], e["en1816"]))
        else:
            partido.append((mi, dest))
    print("─" * 100)

    print("\n➡ MAPEO 1:1 PROPUESTO (una curva de 1816 se lleva ≥80% de mis tickers)")
    print("   Estas se pueden automatizar sin decidir nada: el job vigila esa curva.")
    for mi, c, n, tot in limpio:
        print(f"     {mi:<20} → {c:<34} ({n}/{tot})")
    if partido:
        print("\n⚠ MIS CURVAS QUE SE PARTEN en varias de 1816 — acá hay que DECIDIR")
        print("   (o parto mi curva para seguir a 1816, o el job vigila varias):")
        for mi, dest in partido:
            print(f"     {mi:<20} → " + " · ".join(f"{c} ({n})" for c, n in dest))
    sin = {mi: e["faltan"] for mi, e in porcurva.items() if e["faltan"]}
    if sin:
        print("\n⚠ MÍOS QUE 1816 NO TIENE (quedan a mantenimiento MANUAL siempre):")
        for mi, tks in sorted(sin.items()):
            print(f"     {mi:<20} {', '.join(tks[:12])}"
                  + (f" … (+{len(tks) - 12})" if len(tks) > 12 else ""))


# ── 2) comparación de flujos, familia por familia ────────────────────────────


def _cmp_flujos(doc: dict, cupones: list[dict]) -> dict:
    """Compara MI cuadro contra el de 1816. Devuelve el resumen; no imprime."""
    hoy = date.today().isoformat()
    mios = {_fecha(f.get("fecha")): f for f in (doc.get("flujos") or [])}
    mios.pop("", None)
    por = {
        "efectiva": {_fecha(c.get("fechaPagoEfectiva")): c for c in cupones},
        "teórica": {_fecha(c.get("fechaPagoTeorica")): c for c in cupones},
    }
    for m in por.values():
        m.pop("", None)
    conv = max(por, key=lambda k: len(set(por[k]) & set(mios)))
    suyos = por[conv]

    fut_m = {f: c for f, c in mios.items() if f >= hoy}
    fut_s = {f: c for f, c in suyos.items() if f >= hoy}
    comunes = sorted(set(fut_m) & set(fut_s))

    peor, n_div = 0.0, 0
    for f in comunes:
        m, s = fut_m[f], fut_s[f]
        pares = (
            (_num(m.get("amortizacion_pct", m.get("amortizacion"))),
             _num(s.get("flujoAmortizacion"))),
            (_num(m.get("cupon_sobre_residual", m.get("interes"))),
             _num(s.get("flujoInteres"))),
        )
        for mv, sv in pares:
            if not mv or sv is None:
                continue
            rel = abs(sv / mv - 1)
            if rel > _TOL:
                n_div += 1
                peor = max(peor, rel)
    return {
        "conv": conv, "mios": len(mios), "suyos": len(suyos),
        "fut_mios": len(fut_m), "fut_suyos": len(fut_s), "comunes": len(comunes),
        "divergencias": n_div, "peor": peor,
        "campos_mios": sorted({k for f in (doc.get("flujos") or [{}])[:1] for k in f}),
        "sum_amort_1816": sum(_num(c.get("flujoAmortizacion")) or 0 for c in cupones),
        "primer_mio": next((fut_m[f] for f in comunes[:1]), None),
        "primer_1816": next((fut_s[f] for f in comunes[:1]), None),
    }


def _comparar(master: list[dict], univ: dict, por_familia: int,
              solo_curva: str | None) -> None:
    """Muestra por CADA curva mía: ejemplos de cuadro comparados con 1816."""
    fam: dict[str, list[dict]] = {}
    for d in master:
        if _norm(d.get("ticker_corto")) in univ:
            fam.setdefault((d.get("curva") or "(sin curva)").strip(), []).append(d)

    if solo_curva:
        fam = {k: v for k, v in fam.items() if k.lower() == solo_curva.lower()}
        if not fam:
            print(f"\n⚠ no tengo la curva '{solo_curva}' (o ninguno de sus tickers "
                  "está en 1816)")
            return

    print(f"\n{'=' * 100}\n2) FLUJOS: MI CUADRO vs 1816, familia por familia\n{'=' * 100}")
    veredictos = []
    for mi, docs in sorted(fam.items(), key=lambda x: -len(x[1])):
        # muestra repartida a lo largo de la familia (no los primeros N)
        docs = sorted(docs, key=lambda d: d.get("ticker_corto") or "")
        paso = len(docs) / min(por_familia, len(docs))
        elegidos = [docs[int(i * paso)] for i in range(min(por_familia, len(docs)))]

        print(f"\n── MI CURVA: {mi}  ({len(docs)} instrumentos en 1816, "
              f"muestra {len(elegidos)}) " + "─" * max(0, 30 - len(mi)))
        ok_fam, div_fam = 0, 0
        for d in elegidos:
            tk = _norm(d.get("ticker_corto"))
            inst = univ.get(tk, {})
            try:
                data = mercado_1816.cashflow(tk)
            except Exception as e:
                print(f"  ✘ {tk}: {str(e)[:110]}")
                continue
            cupones = [c for c in (data.get("cashflow") or []) if isinstance(c, dict)]
            if not cupones:
                print(f"  ○ {tk}: 1816 no publica cuadro (0 cupones)")
                continue
            r = _cmp_flujos(d, cupones)
            ok_fam += 1
            div_fam += r["divergencias"]
            print(f"  {tk:<8} → 1816 «{inst.get('_curva', '?')}»")
            print(f"     cupones: míos {r['mios']} / 1816 {r['suyos']} · "
                  f"futuros {r['fut_mios']} / {r['fut_suyos']} · "
                  f"comunes {r['comunes']} (por fecha {r['conv']})")
            print(f"     Σ amortización 1816 = {r['sum_amort_1816']:,.2f} · "
                  f"campos de MI flujo: {', '.join(r['campos_mios'])}")
            if r["primer_mio"] is not None:
                print("     MÍO : " + json.dumps(r["primer_mio"], ensure_ascii=False,
                                                 default=str)[:150])
                print("     1816: " + json.dumps(r["primer_1816"], ensure_ascii=False,
                                                 default=str)[:150])
            if r["divergencias"]:
                print(f"     ⚠ {r['divergencias']} valores fuera del {_TOL:.0%} "
                      f"(peor {r['peor']:.2%})")
            else:
                print(f"     ✔ coincide dentro del {_TOL:.0%}")
        veredictos.append((mi, ok_fam, len(elegidos), div_fam))

    print(f"\n{'FAMILIA MÍA':<20}{'PROBADOS':>10}{'DIVERG.':>9}   VEREDICTO")
    print("─" * 78)
    for mi, ok, tot, div in veredictos:
        v = ("mapeable directo" if ok and not div else
             "REVISAR: hay divergencias" if div else "sin datos para opinar")
        print(f"{mi[:20]:<20}{f'{ok}/{tot}':>10}{div:>9}   {v}")


# ── 3) novedades: lo que 1816 tiene y yo no ──────────────────────────────────


def _novedades(master: list[dict], univ: dict, dias: int, filtro: str | None) -> None:
    """Prototipo de lo que detectaría el job diario: instrumentos de 1816 fuera
    de `mercado.curvas`, por curva y por fecha de emisión."""
    mios = {_norm(d.get("ticker_corto")) for d in master}
    corte = (date.today() - timedelta(days=dias)).isoformat()

    faltan: dict[str, list[dict]] = {}
    for tk, inst in univ.items():
        if tk in mios:
            continue
        c = inst.get("_curva") or "?"
        if filtro and filtro.lower() not in c.lower():
            continue
        faltan.setdefault(c, []).append(inst)

    total = sum(len(v) for v in faltan.values())
    print(f"\n{'=' * 100}\n3) EN 1816 Y NO EN mercado.curvas — qué detectaría el job")
    print(f"{'=' * 100}\n  {total} instrumentos"
          + (f" (filtrado por «{filtro}»)" if filtro else "")
          + f" · los EMITIDOS desde {corte} van marcados 🆕")
    print(f"\n{'CURVA DE 1816':<34}{'FALTAN':>8}{'NUEVOS':>8}   EJEMPLOS (los más recientes)")
    print("─" * 100)
    nuevos_todos: list[tuple[str, dict]] = []
    for c, insts in sorted(faltan.items(), key=lambda x: -len(x[1])):
        insts.sort(key=lambda i: _fecha(i.get("fechaEmision")) or "", reverse=True)
        nuevos = [i for i in insts if (_fecha(i.get("fechaEmision")) or "") >= corte]
        nuevos_todos += [(c, i) for i in nuevos]
        ej = " · ".join(f"{i.get('ticker')}"
                        f"{'🆕' if i in nuevos else ''}" for i in insts[:5])
        print(f"{c[:34]:<34}{len(insts):>8}{len(nuevos):>8}   {ej}")
    print("─" * 100)

    if nuevos_todos:
        print(f"\n➡ EMITIDOS EN LOS ÚLTIMOS {dias} DÍAS que no tengo ({len(nuevos_todos)}) "
              "— esto es lo que el job te avisaría:")
        nuevos_todos.sort(key=lambda x: _fecha(x[1].get("fechaEmision")) or "",
                          reverse=True)
        print(f"  {'TICKER':<10}{'EMISIÓN':<12}{'VTO':<12}{'CURVA 1816':<30}DENOMINACIÓN")
        for c, i in nuevos_todos[:40]:
            print(f"  {(i.get('ticker') or '')[:10]:<10}"
                  f"{_fecha(i.get('fechaEmision')):<12}"
                  f"{_fecha(i.get('fechaVencimiento')):<12}{c[:30]:<30}"
                  f"{(i.get('denominacion') or '')[:40]}")
        if len(nuevos_todos) > 40:
            print(f"  … (+{len(nuevos_todos) - 40} más)")
    else:
        print(f"\n  (ninguno emitido en los últimos {dias} días — probá --dias 365)")


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description="Mapeo mercado.curvas ↔ 1816")
    ap.add_argument("--desde", help="censo guardado por diag_1816_cashflow --json")
    ap.add_argument("--mapeo", action="store_true", help="solo el bloque 1")
    ap.add_argument("--novedades", action="store_true", help="solo el bloque 3 (gratis)")
    ap.add_argument("--por-familia", type=int, default=2,
                    help="tickers por curva MÍA en la comparación de flujos (default 2)")
    ap.add_argument("--curva", help="comparar SOLO esta curva mía (ej: cer)")
    ap.add_argument("--dias", type=int, default=_DIAS_NOVEDAD,
                    help=f"ventana de 'nuevo' en el bloque 3 (default {_DIAS_NOVEDAD})")
    ap.add_argument("--filtro", help="bloque 3: solo curvas de 1816 que contengan esto")
    args = ap.parse_args()

    if not mercado_1816.disponible():
        print("✗ falta MERCADO_1816_API_KEY en el .env")
        return

    print("=" * 100)
    print("1816 ↔ mercado.curvas — INSUMO DE DISEÑO PARA AUTOMATIZAR EL MASTER DE RF")
    print("=" * 100)
    b0 = _saldo()
    if b0:
        d = b0.get("daily") or {}
        print(f"Créditos ANTES · día {d.get('used')}/{d.get('limit')}")

    if args.desde:
        with open(args.desde, encoding="utf-8") as fh:
            censo = json.load(fh)
        print(f"Censo leído de {args.desde} (0 créditos).")
    else:
        censo = censar()
    univ = censo["instrumentos"]
    master = _mi_master()
    print(f"Universo 1816: {len(univ)} tickers · mi master: {len(master)} instrumentos")

    if not master:
        return

    if args.novedades:
        _novedades(master, univ, args.dias, args.filtro)
        return

    _mapeo(master, univ)
    if args.mapeo:
        print("\n(--mapeo: no se compararon flujos ni novedades)")
        return

    b1 = _saldo()
    _comparar(master, univ, max(1, args.por_familia), args.curva)
    b2 = _saldo()
    print(f"\nCosto de la comparación de flujos: {_delta(b1, b2)}")

    _novedades(master, univ, args.dias, args.filtro)

    if b2:
        d = b2.get("daily") or {}
        print(f"\nCréditos DESPUÉS · día {d.get('used')}/{d.get('limit')} · "
              f"total de esta corrida: {_delta(b0, b2)}")


if __name__ == "__main__":
    main()
