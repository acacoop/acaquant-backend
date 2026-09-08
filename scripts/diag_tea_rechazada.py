"""`scripts/diag_tea_rechazada.py` — ¿LA CELDA VACÍA ES «NO SUPE» O ES «NO TE LO CREO»?

Read-only. No escribe nada, no llama a 1816, no toca el motor: recalcula en este
proceso, con las MISMAS funciones de `engines/curvas.py`, y reporta.

**La pregunta.** `mercado.market_snapshot.tea IS NULL` es una sola celda vacía
para tres mundos distintos, y hoy nadie los distingue:

    a) el motor cortó ANTES de la cuenta   → falta un insumo (A3500, MEP, flujos)
    b) el xirr no convergió                → problema numérico
    c) el motor CALCULÓ y DESCARTÓ el número por implausible
       (`if tea is None or not (-0.5 < tea < 50)` y sus cuatro variantes)

El caso (c) es el que rompe la cadena del AV AGENT: `bono_sin_tasa` lee la celda
vacía, la llama «el motor no le calcula la TEA», y el arreglo le pide a 1816 el
mismo número que nuestro propio motor acababa de rechazar — ahora sin guarda,
con un `*` al lado y un tooltip que dice «esta pata todavía no la calcula el
motor». El hallazgo NO puede cerrarse nunca (se cierra cuando aparece la TEA en
el snapshot, que es justamente lo que la guarda impide), así que el ticker queda
en la lista de prioridad pidiéndole 4 campos a 1816 cada 15 minutos, para
siempre.

**Cómo se distinguen sin copiar los umbrales.** No se hardcodea ninguna banda:
se instrumentan `xirr` y `macaulay_duration` (que es lo PRIMERO que corre si la
guarda pasa) y se mira quién se llamó:

    xirr no se llamó                → (a) cortó antes de la cuenta
    xirr devolvió None              → (b) no convergió
    xirr devolvió X, macaulay no    → (c) RECHAZADA, y X es el número descartado
    macaulay corrió y no hay TEA    → excepción posterior (duration/convexity)

Así el día que alguien mueva un umbral, este diag sigue diciendo la verdad.

Costo: 2 queries (master + snapshot) + CPU. Sin escrituras, sin créditos.

Uso:
    python -m scripts.diag_tea_rechazada              # todos los del master
    python -m scripts.diag_tea_rechazada --ticker LUC4O
    python -m scripts.diag_tea_rechazada --todos      # también los que SÍ tienen TEA
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime


def _pct(v, d=1):
    return "--" if v is None else f"{v * 100:.{d}f}%"


def _num(v, d=4):
    return "--" if v is None else f"{v:.{d}f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="", help="un solo bono, por ticker corto")
    ap.add_argument("--todos", action="store_true",
                    help="incluir también los que sí tienen TEA en el snapshot")
    a = ap.parse_args()

    from core import market_snapshot
    from engines import curvas as mc
    from engines._curvas_loader import cargar_indexado_por_ticker

    docs = cargar_indexado_por_ticker()
    if not docs:
        print("el master (`mercado.curvas`) volvió vacío — no hay nada que medir")
        return 1

    simbolos = list(docs)
    snap = market_snapshot.cols_map(
        simbolos, ["last_price", "updated_at", "tea", "paridad",
                   "duration", "mod_duration"])

    cer_dict = mc.cargar_cer()
    dias_habiles = mc.cargar_dias_habiles()
    mep = mc.cargar_mep_actual()
    a3500 = mc.cargar_a3500_actual()
    print(f"master {len(docs)} · snapshot {len(snap)} · MEP {mep} · A3500 {a3500}\n")

    # ── LA INSTRUMENTACIÓN ──────────────────────────────────────────────────
    # Se envuelven las funciones REALES (no se reimplementan): la cuenta que
    # corre acá es exactamente la del motor, y lo único que se agrega es un
    # apunte de qué se llamó y qué devolvió.
    visto: dict = {}
    xirr_real, mac_real = mc.xirr, mc.macaulay_duration

    def xirr_espia(fechas, flujos):
        r = xirr_real(fechas, flujos)
        visto["xirr"] = r
        visto["xirr_llamado"] = True
        return r

    def mac_espia(*args, **kw):
        visto["mac_llamado"] = True
        return mac_real(*args, **kw)

    mc.xirr, mc.macaulay_duration = xirr_espia, mac_espia

    try:
        filas = []
        for simbolo, d in docs.items():
            tc = (d.get("ticker_corto") or "").strip()
            if a.ticker and tc.upper() != a.ticker.strip().upper():
                continue
            m = snap.get(simbolo) or {}
            precio = m.get("last_price")
            if not precio:
                continue                      # sin precio no hay nada que juzgar
            if m.get("tea") is not None and not a.todos:
                continue

            visto.clear()
            campos = mc.calcular_campos(
                {"ticker": simbolo, "price": precio,
                 "timestamp": m.get("updated_at") or datetime.utcnow()},
                d, cer_dict, dias_habiles, mep, a3500)

            rama = mc.rama_calculo(d)
            if campos and "TEA" in campos:
                veredicto, cruda = "OK (el motor la calcula)", campos["TEA"]
            elif not visto.get("xirr_llamado"):
                veredicto, cruda = "(a) CORTÓ ANTES de la cuenta", None
            elif visto.get("xirr") is None:
                veredicto, cruda = "(b) el xirr NO CONVERGIÓ", None
            elif not visto.get("mac_llamado"):
                veredicto, cruda = "(c) RECHAZADA por la guarda", visto.get("xirr")
            else:
                veredicto, cruda = "(d) excepción DESPUÉS del xirr", visto.get("xirr")

            filas.append({
                "tk": tc, "rama": rama, "precio": precio, "veredicto": veredicto,
                "cruda": cruda, "tea_snap": m.get("tea"),
                "paridad": m.get("paridad"), "dur": m.get("duration"),
                "mod_dur": m.get("mod_duration"),
                "vto": (d.get("fecha_vencimiento") or "")[:10],
            })
    finally:
        mc.xirr, mc.macaulay_duration = xirr_real, mac_real

    if not filas:
        print("no hay ningún bono con precio y sin TEA — nada que reportar")
        return 0

    # ── LO QUE 1816 DICE DE LOS MISMOS BONOS ────────────────────────────────
    from agente import tasa_1816
    t1816 = tasa_1816.tasas()
    pendientes = tasa_1816.pendientes()

    orden = {"(c)": 0, "(d)": 1, "(b)": 2, "(a)": 3, "OK": 4}
    filas.sort(key=lambda f: (orden.get(f["veredicto"][:3], 9), f["tk"]))

    print(f"{'TICKER':<8}{'RAMA':<13}{'VTO':<12}{'PRECIO':>13}  "
          f"{'VEREDICTO':<32}{'TEA CRUDA':>11}{'TEA 1816':>11}{'PARIDAD':>10}"
          f"{'DUR':>8}{'MODDUR':>8}")
    print("─" * 128)
    for f in filas:
        ta = (t1816.get(f["tk"]) or {}).get("tea")
        print(f"{f['tk']:<8}{f['rama']:<13}{f['vto']:<12}{f['precio']:>13,.2f}  "
              f"{f['veredicto']:<32}{_pct(f['cruda']):>11}{_pct(ta):>11}"
              f"{_num(f['paridad'], 2):>10}{_num(f['dur'], 3):>8}"
              f"{_num(f['mod_dur'], 3):>8}")

    # ── EL RESUMEN, que es lo que decide qué se hace ────────────────────────
    print()
    por_veredicto: dict[str, int] = {}
    for f in filas:
        por_veredicto[f["veredicto"]] = por_veredicto.get(f["veredicto"], 0) + 1
    for v, n in sorted(por_veredicto.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3}  {v}")

    rechazadas = [f for f in filas if f["veredicto"].startswith("(c)")]
    con_1816 = [f for f in rechazadas if (t1816.get(f["tk"]) or {}).get("tea") is not None]
    print(f"\nrechazadas por la guarda: {len(rechazadas)} · de esas, con tasa de "
          f"1816 ya publicada en pantalla: {len(con_1816)}")
    if con_1816:
        print("  (esas filas muestran hoy, con un `*`, un número que nuestro "
              "propio motor descartó por implausible)")
        for f in con_1816:
            ta = (t1816.get(f["tk"]) or {}).get("tea")
            delta = None if ta is None or f["cruda"] is None else abs(ta - f["cruda"])
            print(f"    {f['tk']:<8} nuestra {_pct(f['cruda'])} · 1816 {_pct(ta)}"
                  f" · |delta| {_pct(delta, 2)}")
    print(f"\nen la lista de prioridad ahora mismo: {len(pendientes)} tickers "
          f"× 4 campos, cada 15 min de rueda ({', '.join(pendientes[:20])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
