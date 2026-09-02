"""`scripts/diag_tna.py` — ¿LA TNA DE RENTA FIJA ESTÁ VIVA, O ES LA MISMA DE SIEMPRE?

Read-only. Contesta con números las tres preguntas que hoy se contestan mirando
la pantalla, que es donde no se distinguen:

  1. **¿Está clavada?** Un bono que no opera conserva su TEA: el motor solo
     recalcula cuando cambia el `last_price` (`engines/curvas.py`, cache
     `ultimo_calculado`), y la vista no muestra de cuándo es el número. Una TNA
     de hace tres semanas y una de hace tres segundos se dibujan **idénticas**.
  2. **¿Se rompió algo en estos días?** Saltos día contra día en la serie de
     cierre, con la FECHA exacta del salto, y los que pasaron a `--` o volvieron.
  3. **¿El cálculo cierra consigo mismo?** La TEM guardada contra la que se
     deriva de la TEA de la misma fila, y la TNA de 1816 contra su propia TEA.
     Dos columnas escritas en momentos distintos no fallan: se contradicen.

La serie sale de `mercado.snapshots_cierre_hist` (una fila por bono y día, la
escribe `jobs.snapshot_cierre` **solo si el bono operó**), así que la ausencia de
un bono ahí NO es un problema: es que no operó. Eso también se informa.

Uso:
    python -m scripts.diag_tna                     # 15 días, salto ≥ 5 pp
    python -m scripts.diag_tna --dias 30 --salto 3
    python -m scripts.diag_tna --ticker AL30       # el detalle de UN bono
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from itertools import pairwise

# Lo que muestra la pantalla: TNA a UN decimal (`bonos-table.tsx`). Comparar a
# 6 decimales diría "cambió" de un bono que el usuario ve igual toda la semana —
# y la pregunta es justamente por lo que se VE.
DEC_PANTALLA = 1


def _tna(tea):
    """TNA nominal anual con capitalización mensual — `quant/tasas.py`."""
    if tea is None or tea <= -1:
        return None
    return ((1 + float(tea)) ** (1 / 12) - 1) * 12


def _pct(v, d=2):
    return "--" if v is None else f"{v * 100:.{d}f}%"


def _q(sql: str, params: tuple = ()) -> list[tuple]:
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# ─────────────────────────────────────────────────────────────────────────────
# 1. LA FOTO DE HOY — qué tiene tasa y qué no
# ─────────────────────────────────────────────────────────────────────────────
def foto_hoy() -> list[dict]:
    """El master de bonos con su fila del snapshot. MISMO join que la vista.

    ⚠️ `c.instrumento` es el SÍMBOLO DE MERCADO y `c.ticker` el corto — están
    invertidos respecto de lo que dicen los nombres (renombre 2026-08-15). Se
    joinea por `instrumento`, igual que `curvas_vista._bonos_crudos`: joinear por
    el otro no da error, da cero matches.
    """
    rows = _q(
        "SELECT c.ticker, c.instrumento, c.curva, c.ajuste, c.emisor_tipo, "
        "       c.fecha_vencimiento, s.tea, s.tem, s.duration, s.last_price, "
        "       s.total_nominals, s.updated_at, "
        "       EXTRACT(EPOCH FROM (now() - s.updated_at))::int "
        "  FROM mercado.curvas c "
        "  LEFT JOIN mercado.market_snapshot s ON s.ticker = c.instrumento "
        " ORDER BY c.ticker",
    )
    return [{"tk": r[0], "simbolo": r[1], "curva": r[2], "ajuste": r[3],
             "emisor_tipo": r[4], "vto": r[5], "tea": r[6], "tem": r[7],
             "duration": r[8], "last": r[9], "vol": r[10], "upd": r[11],
             "edad_s": r[12]} for r in rows]


def bloque_hoy(f: list[dict]) -> None:
    print("\n" + "=" * 78)
    print("1. LA FOTO DE HOY — ¿quién tiene TNA?")
    print("=" * 78)
    con_tea = [b for b in f if b["tea"] is not None]
    sin_fila = [b for b in f if b["upd"] is None]
    con_precio_sin_tea = [b for b in f
                          if b["tea"] is None and (b["last"] or 0) > 0]
    print(f"  bonos en mercado.curvas .................. {len(f)}")
    print(f"  con fila en market_snapshot .............. {len(f) - len(sin_fila)}")
    print(f"  SIN fila en snapshot (no suscripto) ...... {len(sin_fila)}")
    print(f"  con TEA (o sea, con TNA en pantalla) ..... {len(con_tea)}")
    print(f"  con PRECIO y SIN TEA (salen en «--») ..... {len(con_precio_sin_tea)}")

    # La edad del `updated_at` NO es la edad del precio: `valores.py` reescribe la
    # fila cada ~45s para todo lo SUSCRIPTO, opere o no. Por eso una edad alta
    # significa una sola cosa, y es la grave: ese símbolo no lo está mirando nadie.
    viejos = sorted((b for b in f if b["edad_s"] and b["edad_s"] > 3600),
                    key=lambda b: -b["edad_s"])
    print(f"\n  filas que el motor NO refresca hace >1h .. {len(viejos)}"
          "   (el motor reescribe cada ~45s lo que tiene suscripto,")
    print("                                                así que esto es "
          "«nadie está mirando ese símbolo», no «no operó»)")
    for b in viejos[:15]:
        print(f"     {b['tk']:<10} {b['simbolo']:<28} "
              f"hace {b['edad_s'] // 3600:>4}h   TNA {_pct(_tna(b['tea']), 1)}")
    if len(viejos) > 15:
        print(f"     … y {len(viejos) - 15} más")


# ─────────────────────────────────────────────────────────────────────────────
# 2. COHERENCIA — la fila contra sí misma
# ─────────────────────────────────────────────────────────────────────────────
def bloque_coherencia(f: list[dict]) -> None:
    print("\n" + "=" * 78)
    print("2. ¿EL CÁLCULO CIERRA CONSIGO MISMO? (TEM guardada vs TEM de la TEA)")
    print("=" * 78)
    print("  Las dos las escribe el MISMO write del motor. Si difieren, alguna")
    print("  quedó de un cálculo anterior — y la pantalla deriva la TNA de la TEA")
    print("  pero muestra la TEM de la columna: dos números que no se hablan.")
    malas = []
    for b in f:
        if b["tea"] is None or b["tem"] is None:
            continue
        esperado = (1 + float(b["tea"])) ** (1 / 12) - 1
        if abs(esperado - float(b["tem"])) > 1e-6:
            malas.append((b, esperado))
    print(f"\n  filas con TEA y TEM ..................... "
          f"{sum(1 for b in f if b['tea'] is not None and b['tem'] is not None)}")
    print(f"  filas donde NO cierran (>1e-6) .......... {len(malas)}")
    for b, esp in malas[:20]:
        print(f"     {b['tk']:<10} TEA {_pct(b['tea'])}  TEM guardada "
              f"{_pct(b['tem'])}  TEM correcta {_pct(esp)}")

    # Una TEA sin duration deja la fila sin MOD DUR y sin poder decidir si es
    # ruido (`curvas_vista.es_tasa_ruido` mira duration): la tasa se muestra
    # como comparable sin que nada haya podido chequear que lo sea.
    sin_dur = [b for b in f if b["tea"] is not None and b["duration"] is None]
    print(f"\n  con TEA y SIN duration .................. {len(sin_dur)}"
          "   (no se les puede aplicar el filtro de ruido)")
    for b in sin_dur[:10]:
        print(f"     {b['tk']:<10} TNA {_pct(_tna(b['tea']), 1)}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. LA SERIE — clavadas y saltos
# ─────────────────────────────────────────────────────────────────────────────
def serie(dias: int) -> dict[str, list[dict]]:
    """`ticker_corto → [{fecha, tea, precio, vol}]` ordenado por fecha.

    Scopeado por fecha (REGLA #4): la tabla es chica pero se filtra igual.
    """
    rows = _q(
        "SELECT ticker_corto, ticker, fecha, tea, ultimo_precio, "
        "       total_nominals_dia "
        "  FROM mercado.snapshots_cierre_hist "
        " WHERE fecha >= current_date - %s "
        " ORDER BY ticker_corto, fecha", (dias,))
    out: dict[str, list[dict]] = defaultdict(list)
    for tc, tk, fecha, tea, px, vol in rows:
        out[tc or tk].append({"fecha": fecha, "tea": tea, "precio": px,
                              "vol": vol})
    return out


def bloque_clavadas(s: dict[str, list[dict]], dias: int) -> None:
    print("\n" + "=" * 78)
    print(f"3. ¿ESTÁ CLAVADA? — la TNA de cierre, últimos {dias} días")
    print("=" * 78)
    print("  Solo entran los bonos que OPERARON ese día (`jobs.snapshot_cierre`")
    print("  saltea volumen 0), así que estos son los que SÍ tuvieron precio")
    print("  nuevo. Si igual la TNA no se movió, no es falta de mercado.")
    if not s:
        print("\n  ⚠️  snapshots_cierre_hist vacía en la ventana. O no corrió")
        print("      `jobs.snapshot_cierre`, o no hubo ruedas. Nada que medir.")
        return

    filas = []
    for tc, ps in s.items():
        tnas = [round((_tna(p["tea"]) or 0) * 100, DEC_PANTALLA)
                if p["tea"] is not None else None for p in ps]
        vistos = [t for t in tnas if t is not None]
        if len(vistos) < 2:
            continue
        # Racha final: cuántos días seguidos, hasta el último, muestra lo mismo.
        racha, ult = 1, tnas[-1]
        for t in reversed(tnas[:-1]):
            if t != ult:
                break
            racha += 1
        filas.append({"tk": tc, "dias": len(vistos), "distintos": len(set(vistos)),
                      "racha": racha, "tna": ult, "ps": ps})

    filas.sort(key=lambda x: (-x["racha"], -x["dias"]))
    idem = [x for x in filas if x["distintos"] == 1 and x["dias"] >= 3]
    print(f"\n  bonos con ≥2 cierres en la ventana ....... {len(filas)}")
    print(f"  con UN SOLO valor de TNA en ≥3 ruedas .... {len(idem)}"
          "   ← «siempre igual» medido")
    print(f"\n  {'TICKER':<10} {'RUEDAS':>7} {'VALORES':>8} {'RACHA':>6}  TNA HOY")
    for x in filas[:25]:
        print(f"  {x['tk']:<10} {x['dias']:>7} {x['distintos']:>8} "
              f"{x['racha']:>6}  {x['tna']}%")
    if len(filas) > 25:
        print(f"  … y {len(filas) - 25} más")


def bloque_saltos(s: dict[str, list[dict]], salto: float) -> None:
    print("\n" + "=" * 78)
    print(f"4. ¿SE ROMPIÓ ALGO? — saltos de TNA ≥ {salto} pp entre ruedas")
    print("=" * 78)
    ev = []
    for tc, ps in s.items():
        for a, b in pairwise(ps):
            ta, tb = _tna(a["tea"]), _tna(b["tea"])
            if ta is None and tb is not None:
                ev.append((b["fecha"], tc, "APARECIÓ", None, tb, a, b))
            elif ta is not None and tb is None:
                ev.append((b["fecha"], tc, "SE APAGÓ", ta, None, a, b))
            elif ta is not None and tb is not None:
                d = abs(tb - ta) * 100
                if d >= salto:
                    ev.append((b["fecha"], tc, f"{d:.1f} pp", ta, tb, a, b))
    ev.sort(key=lambda e: (e[0], e[1]), reverse=True)
    print(f"\n  eventos en la ventana .................... {len(ev)}")
    if not ev:
        print("  (ninguno: la serie no tiene roturas en esta ventana)")
        return
    print(f"\n  {'FECHA':<12} {'TICKER':<10} {'QUÉ':<10} {'TNA ANTES':>10} "
          f"{'TNA DESPUÉS':>12}  PRECIO")
    for fecha, tc, que, ta, tb, a, b in ev[:40]:
        pa = "--" if a["precio"] is None else f"{float(a['precio']):.2f}"
        pb = "--" if b["precio"] is None else f"{float(b['precio']):.2f}"
        print(f"  {fecha!s:<12} {tc:<10} {que:<10} {_pct(ta, 1):>10} "
              f"{_pct(tb, 1):>12}  {pa} → {pb}")
    if len(ev) > 40:
        print(f"  … y {len(ev) - 40} más")
    print("\n  ⚠️  Un salto de TNA con el PRECIO casi igual no es mercado: es el")
    print("      cálculo. Al revés (precio que salta) es un trade real o un")
    print("      precio mal cargado — se mira el bono, no el motor.")


# ─────────────────────────────────────────────────────────────────────────────
# 5. ¿LA DE HOY ES LA DEL ÚLTIMO CIERRE?
# ─────────────────────────────────────────────────────────────────────────────
def bloque_vs_cierre(f: list[dict], s: dict[str, list[dict]]) -> None:
    print("\n" + "=" * 78)
    print("5. LA TNA DE AHORA vs LA DEL ÚLTIMO CIERRE QUE TIENE")
    print("=" * 78)
    print("  Idéntica a 6 decimales = el motor no la recalculó desde esa fecha.")
    print("  Cuanto más vieja esa fecha, más viejo el precio que hay detrás.")
    iguales = []
    for b in f:
        if b["tea"] is None:
            continue
        ps = [p for p in s.get(b["tk"], []) if p["tea"] is not None]
        if not ps:
            continue
        ult = ps[-1]
        if abs(float(b["tea"]) - float(ult["tea"])) < 1e-9:
            iguales.append((b, ult))
    iguales.sort(key=lambda x: x[1]["fecha"])
    print(f"\n  con TEA idéntica a su último cierre ...... {len(iguales)}")
    print(f"\n  {'TICKER':<10} {'ÚLT. CIERRE':<12} {'TNA':>8}  PRECIO")
    for b, ult in iguales[:30]:
        px = "--" if ult["precio"] is None else f"{float(ult['precio']):.2f}"
        print(f"  {b['tk']:<10} {ult['fecha']!s:<12} "
              f"{_pct(_tna(b['tea']), 1):>8}  {px}")
    if len(iguales) > 30:
        print(f"  … y {len(iguales) - 30} más")


# ─────────────────────────────────────────────────────────────────────────────
# 6. LAS OTRAS FUENTES DE TNA
# ─────────────────────────────────────────────────────────────────────────────
def bloque_1816() -> None:
    print("\n" + "=" * 78)
    print("6. LAS TNA QUE NO CALCULA EL MOTOR (1816)")
    print("=" * 78)
    print("  `mercado.tamar_1816` es la ÚNICA fuente que manda TNA propia: donde")
    print("  hay fila, la pantalla usa ESA y no la deriva. Si su convención no es")
    print("  TEM×12, esa fila muestra una TNA que no cierra con su propia TEM.")
    try:
        rows = _q("SELECT ticker, pata, tea, tna, spread, fecha_operacion, "
                  "       actualizado_en, "
                  "       EXTRACT(EPOCH FROM (now() - actualizado_en))::int "
                  "  FROM mercado.tamar_1816 ORDER BY ticker, pata")
    except Exception as e:
        print(f"  no pude leer mercado.tamar_1816: {e}")
        rows = []
    print(f"\n  filas ................................... {len(rows)}")
    if rows:
        edades = [r[7] for r in rows if r[7] is not None]
        if edades:
            print(f"  más vieja ............................... "
                  f"{max(edades) // 60} min   (el job corre cada 30')")
        fechas = {str(r[5]) for r in rows if r[5]}
        print(f"  fechas de operación en la tabla ......... "
              f"{', '.join(sorted(fechas)) or '--'}")
        print(f"\n  {'TICKER':<10} {'PATA':<14} {'TEA':>8} {'TNA 1816':>9} "
              f"{'TNA=TEM×12':>11} {'MARGEN':>8}")
        for tk, pata, tea, tna, spread, _f, _a, _e in rows:
            der = _tna(tea)
            marca = ""
            if tna is not None and der is not None and abs(float(tna) - der) > 5e-4:
                marca = "  ← no cierra"
            print(f"  {tk:<10} {pata:<14} {_pct(tea, 1):>8} {_pct(tna, 1):>9} "
                  f"{_pct(der, 1):>11} {_pct(spread, 2):>8}{marca}")

    try:
        ag = _q("SELECT ticker, pata, tea, fecha_1816, pedido_at, "
                "       EXTRACT(EPOCH FROM (now() - pedido_at))::int "
                "  FROM agente.tasa_1816 ORDER BY ticker")
    except Exception as e:
        print(f"\n  no pude leer agente.tasa_1816: {e}")
        return
    print(f"\n  agente.tasa_1816 (el último recurso) ..... {len(ag)} filas")
    for tk, pata, tea, f1816, _p, edad in ag[:20]:
        print(f"     {tk:<10} {pata or '-':<10} TNA {_pct(_tna(tea), 1):>8}  "
              f"1816 {f1816!s:<12} pedida hace {(edad or 0) // 3600}h")


# ─────────────────────────────────────────────────────────────────────────────
def detalle(tk: str, dias: int) -> None:
    print("\n" + "=" * 78)
    print(f"DETALLE DE {tk}")
    print("=" * 78)
    f = [b for b in foto_hoy() if b["tk"].upper() == tk.upper()]
    if not f:
        print(f"  {tk} no está en mercado.curvas.")
        return
    b = f[0]
    print(f"  símbolo de mercado ...... {b['simbolo']}")
    print(f"  curva / ajuste .......... {b['curva']} / {b['ajuste']}")
    print(f"  vencimiento ............. {b['vto']}")
    print(f"  last_price .............. {b['last']}")
    print(f"  volumen del día ......... {b['vol']}")
    print(f"  TEA / TEM / duration .... {_pct(b['tea'])} / {_pct(b['tem'])} "
          f"/ {b['duration']}")
    print(f"  TNA en pantalla ......... {_pct(_tna(b['tea']), 1)}")
    print(f"  fila refrescada hace .... {b['edad_s']} s")
    ps = serie(dias).get(tk.upper(), [])
    print(f"\n  serie de cierre ({len(ps)} ruedas):")
    print(f"  {'FECHA':<12} {'PRECIO':>10} {'TNA':>8} {'VOLUMEN':>14}")
    for p in ps:
        px = "--" if p["precio"] is None else f"{float(p['precio']):.2f}"
        vol = "--" if p["vol"] is None else f"{float(p['vol']):,.0f}"
        print(f"  {p['fecha']!s:<12} {px:>10} {_pct(_tna(p['tea']), 1):>8} "
              f"{vol:>14}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=15,
                    help="ventana de la serie de cierre (default 15)")
    ap.add_argument("--salto", type=float, default=5.0,
                    help="salto de TNA en puntos porcentuales que se reporta")
    ap.add_argument("--ticker", help="detalle de UN bono")
    args = ap.parse_args()

    if args.ticker:
        detalle(args.ticker, args.dias)
        return 0

    f = foto_hoy()
    s = serie(args.dias)
    bloque_hoy(f)
    bloque_coherencia(f)
    bloque_clavadas(s, args.dias)
    bloque_saltos(s, args.salto)
    bloque_vs_cierre(f, s)
    bloque_1816()
    print("\n" + "=" * 78)
    print("LEER ASÍ: bloque 3 contesta «¿siempre igual?»; bloque 4 «¿qué se")
    print("rompió y qué día?»; bloque 5 «¿hace cuánto que ese número no se")
    print("recalcula?». Los bloques 1, 2 y 6 dicen si la culpa es del motor,")
    print("de la fila o del proveedor.")
    print("=" * 78 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
