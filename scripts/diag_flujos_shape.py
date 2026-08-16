"""scripts/diag_flujos_shape.py — ¿la rama nueva sabe LEER los flujos de este bono? READ-ONLY.

Sale de lo que descubrió `diag_motor_ejes`: 18 bonos cambiarían de rama de cálculo.
Pero al mirar el código de las ramas apareció un obstáculo que no estaba en el
plan — **las ramas no se diferencian solo por la MATEMÁTICA, también por CÓMO
LEEN LOS FLUJOS**:

    rama `soberanos` → flujos en PORCENTAJE   (amortizacion_pct + cupon_sobre_residual)
    rama `cer`       → flujos en PORCENTAJE   (idem + cer_emision)
    rama `on`        → flujos en ABSOLUTO     (amortizacion + interes, por 100 VN)
    rama `tasa_fija` → bullet (`flujo_vencimiento`) o cronograma absoluto

Y el formato **no es una propiedad de los ejes**: es cómo se cargó ese bono en su
día. O sea que mover un BOPREAL de la rama `on` a la rama `soberanos` puede dejarlo
sin TEA aunque su clasificación sea perfecta — la rama nueva busca claves que sus
flujos no tienen. No da error: da un número raro, o ninguno.

**Qué contesta este diag, por bono:** qué formato tienen sus flujos HOY, si tiene
`cer_emision`, y qué TEA/precio muestra ahora mismo. Con eso se sabe, para cada
uno de los que cambiarían de rama, si el cambio es un RENOMBRE (la rama nueva lee
igual) o una MIGRACIÓN DE DATOS (hay que convertir los flujos primero).

Prioridad de la mesa: **soberanos y BCRA primero** — el resto se corrige después.
Por eso el reporte sale agrupado y con esos dos arriba.

READ-ONLY. No escribe, no borra, no toca los motores.

Uso:
    python -m scripts.diag_flujos_shape
"""
from __future__ import annotations

from core.postgres import get_pool

_SEP = "=" * 100

# Claves que delatan cada formato. Un flujo puede traer varias; lo que importa es
# cuál de los DOS grupos está presente.
_PCT = ("amortizacion_pct", "cupon_sobre_residual", "residual_previo_pct")
_ABS = ("amortizacion", "interes")


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _shape(doc: dict) -> str:
    """El formato de los flujos de un bono, mirando el doc REAL."""
    flujos = doc.get("flujos") or []
    if not flujos:
        return "bullet" if doc.get("flujo_vencimiento") else "SIN FLUJOS"
    claves: set[str] = set()
    for f in flujos:
        claves |= set(f.keys())
    tiene_pct = bool(claves & set(_PCT))
    tiene_abs = bool(claves & set(_ABS))
    if tiene_pct and tiene_abs:
        return "MIXTO"        # trae las dos → hay que mirarlo a mano
    if tiene_pct:
        return "porcentual"
    if tiene_abs:
        return "absoluto"
    return "DESCONOCIDO"


# Qué formato ESPERA cada rama (espejo de engines/curvas.py).
_ESPERA = {
    "soberanos": "porcentual",
    "cer": "porcentual",
    "on": "absoluto",
    "tasa_fija": "absoluto",     # o bullet
    "solo_duration": None,       # no lee flujos
}


def _rama_por_ejes(f: dict) -> str:
    emisor, moneda, ajuste = f.get("emisor_tipo"), f.get("moneda_eje"), f.get("ajuste")
    if not (emisor and moneda and ajuste):
        return "sin_ejes"
    if emisor == "corporativo":
        return "on"
    if ajuste == "cer":
        return "cer"
    if ajuste == "dolar_linked":
        return "dolar_linked"
    if ajuste == "fija":
        return "soberanos" if moneda in ("USD", "EUR") else "tasa_fija"
    return "solo_duration"


def _rama_hoy(curva: str | None) -> str:
    c = (curva or "").strip()
    if c in ("tasa_fija", "cer", "soberanos", "dolar_linked"):
        return c
    if c == "on" or c.startswith("on_"):
        return "on"
    return "solo_duration"


def _veredicto(shape: str, rama_nueva: str) -> str:
    """¿Mover este bono de rama es gratis o hay que convertir sus flujos?"""
    espera = _ESPERA.get(rama_nueva, "?")
    if rama_nueva in ("solo_duration", "sin_ejes"):
        return "PIERDE TEA"
    if espera is None:
        return "—"
    if shape in ("SIN FLUJOS", "DESCONOCIDO", "MIXTO"):
        return f"REVISAR ({shape})"
    if shape == "bullet":
        return "OK" if rama_nueva == "tasa_fija" else "MIGRAR flujos"
    if rama_nueva == "dolar_linked":
        return "—"
    return "OK" if shape == espera else "MIGRAR flujos"


def main() -> None:
    print(_SEP)
    print("FORMATO DE FLUJOS — ¿la rama nueva sabe leer este bono?")
    print(_SEP)

    filas = _q("""
        SELECT ticker, curva, emisor_tipo, moneda_eje, ajuste, ajuste_alt,
               emisor, data
        FROM mercado.curvas ORDER BY ticker
    """)
    snap = {r["ticker"]: r for r in _q(
        "SELECT ticker, last_price, tea FROM mercado.market_snapshot")}

    # El snapshot se indexa por el SÍMBOLO de mercado; el master por ticker corto.
    por_simbolo = {}
    for f in filas:
        doc = f.get("data") or {}
        por_simbolo[f["ticker"]] = (doc.get("ticker") or "")

    # ── Panorama: formato × rama de HOY (¿está todo consistente hoy?) ────────
    print("\n  ── Panorama: el formato que tiene cada rama HOY ──\n")
    combo: dict[tuple[str, str], int] = {}
    for f in filas:
        combo[(_rama_hoy(f["curva"]), _shape(f.get("data") or {}))] = \
            combo.get((_rama_hoy(f["curva"]), _shape(f.get("data") or {})), 0) + 1
    print(f"  {'RAMA HOY':<16}{'FORMATO':<16}{'N':>5}   {'ESPERA':<12}")
    print("  " + "-" * 55)
    for (rama, sh), n in sorted(combo.items(), key=lambda kv: (kv[0][0], -kv[1])):
        esp = _ESPERA.get(rama)
        ok = "  " if (esp is None or sh == esp or sh == "bullet") else "⚠ "
        print(f"  {ok}{rama:<14}{sh:<16}{n:>5}   {esp or '—'!s:<12}")

    # ── Los que cambiarían de rama, con veredicto ───────────────────────────
    cambian = []
    for f in filas:
        h, n = _rama_hoy(f["curva"]), _rama_por_ejes(f)
        if h != n:
            doc = f.get("data") or {}
            sh = _shape(doc)
            cambian.append({
                "ticker": f["ticker"], "curva": f["curva"], "hoy": h, "nueva": n,
                "ejes": f"{f['emisor_tipo'] or '—'}/{f['moneda_eje'] or '—'}/{f['ajuste'] or '—'}",
                "shape": sh, "veredicto": _veredicto(sh, n),
                "cer_em": doc.get("cer_emision"),
                "tea": (snap.get(por_simbolo.get(f["ticker"], "")) or {}).get("tea"),
                "px": (snap.get(por_simbolo.get(f["ticker"], "")) or {}).get("last_price"),
                "emisor_tipo": f["emisor_tipo"],
            })

    # PRIORIDAD DE LA MESA: soberano y bcra arriba, el resto abajo.
    def _orden(c: dict) -> tuple:
        pri = 0 if c["emisor_tipo"] in ("soberano", "bcra") else 1
        return (pri, c["nueva"], c["ticker"])

    print(f"\n{_SEP}\n  LOS QUE CAMBIAN DE RAMA — ¿renombre o migración de datos?\n{_SEP}")
    print("  Prioridad de la mesa: SOBERANO y BCRA primero.\n")
    print(f"  {'TICKER':<9}{'HOY':<9}{'→ NUEVA':<15}{'EJES':<26}"
          f"{'FORMATO':<13}{'VEREDICTO':<18}{'TEA hoy':>9}")
    print("  " + "-" * 98)
    pri_ant = None
    for c in sorted(cambian, key=_orden):
        pri = "SOBERANO / BCRA" if c["emisor_tipo"] in ("soberano", "bcra") else "el resto"
        if pri != pri_ant:
            print(f"\n  ▸ {pri}")
            pri_ant = pri
        tea = f"{float(c['tea']) * 100:.2f}%" if c["tea"] is not None else "--"
        print(f"  {str(c['ticker'])[:8]:<9}{c['hoy']:<9}{'→ ' + c['nueva']:<15}"
              f"{c['ejes'][:25]:<26}{c['shape']:<13}{c['veredicto']:<18}{tea:>9}")

    # ── Los 9 sin ejes ───────────────────────────────────────────────────────
    sin = [f for f in filas if _rama_por_ejes(f) == "sin_ejes"]
    if sin:
        print(f"\n{_SEP}\n  SIN EJES ({len(sin)}) — hoy calculan por su palabra; migrados perderían la TEA\n{_SEP}")
        print(f"  {'TICKER':<9}{'CURVA HOY':<14}{'FORMATO':<13}{'TEA hoy':>9}   EMISOR")
        print("  " + "-" * 80)
        for f in sin:
            doc = f.get("data") or {}
            m = snap.get(por_simbolo.get(f["ticker"], "")) or {}
            tea = f"{float(m['tea']) * 100:.2f}%" if m.get("tea") is not None else "--"
            print(f"  {str(f['ticker'])[:8]:<9}{str(f['curva'] or '—')[:13]:<14}"
                  f"{_shape(doc):<13}{tea:>9}   {str(f['emisor'] or '—')[:32]}")
        print("\n  Los que hoy muestran TEA son los urgentes: migrar sin clasificarlos")
        print("  primero los dejaría en blanco.")

    # ── Resumen accionable ───────────────────────────────────────────────────
    print(f"\n{_SEP}\n  RESUMEN\n{_SEP}")
    for v in ("OK", "MIGRAR flujos", "PIERDE TEA"):
        ns = [c["ticker"] for c in cambian if c["veredicto"] == v]
        if ns:
            print(f"  {v:<18}{len(ns):>3}   {', '.join(str(x) for x in ns)}")
    otros = [c for c in cambian if c["veredicto"].startswith("REVISAR")]
    if otros:
        print(f"  {'REVISAR a mano':<18}{len(otros):>3}   "
              f"{', '.join(str(c['ticker']) + ' (' + c['shape'] + ')' for c in otros)}")

    print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")


if __name__ == "__main__":
    main()
