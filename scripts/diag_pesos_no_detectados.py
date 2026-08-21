"""¿POR QUÉ EL DETECTOR CANTA 3 Y NO TODOS LOS QUE COTIZAN EN PESOS?

    python -m scripts.diag_pesos_no_detectados

Read-only. Nace de lo que el user vio en pantalla (2026-08-22): la grilla de
los BPO muestra 156.150,00 / 139.600,00 —pesos, sin ninguna duda— y el agente
solo cantó **BPOD7, NDT25 y SFD34**. *«Están literalmente todos cotizando en
pesos y me dice de uno solo.»*

**No se puede contestar desde el código**: `detectar_precio_moneda` tiene SEIS
puertas seguidas y cualquiera de ellas puede estar dejando pasar al bono, por
motivos completamente distintos (uno sería un bug, otro sería correcto). Este
diag las recorre **en el mismo orden** y dice, bono por bono, **cuál lo frenó**.

Lo que hace distinto a una corrida del detector: el detector devuelve lo que
encontró; esto devuelve **por qué no encontró el resto**, que es la pregunta.

LAS PUERTAS, EN ORDEN
=====================

  1. `moneda_eje != USD`      no es una curva en dólares → no aplica
  2. `dolar_linked`           paga en pesos POR DEFINICIÓN → no es un problema
  3. sin precio               lo canta otro detector
  4. paridad cruda en rango   el precio YA viene en dólares → está bien
  5. ÷MEP no lo arregla       entonces no es un tema de moneda
  6. el DETECTOR decide       **y acá se pregunta, no se reimplementa**

⚠️⚠️ **LA PUERTA 6 NO SE COPIA — SE LE PREGUNTA AL DETECTOR.** La primera
versión de este script reimplementaba las seis, incluida la que decide. Cuando
el detector se arregló (§0.bu), el diag **siguió midiendo su propia copia
vieja** y siguió diciendo «11 no se cantan» cuando ya se cantaban.

O sea: la herramienta que existe para cazar REGLA #9 **tenía REGLA #9
adentro**, y de la peor forma posible — no falló, contestó con seguridad usando
el dato equivocado. Ahora el veredicto sale de `detectar_precio_fuera_de_moneda`
y la caminata por las puertas queda solo para EXPLICAR dónde cae cada bono, que
es lo que un `for` sobre los hallazgos no puede decir.

MEDIDO EN PROD (2026-08-22) — Y LA HIPÓTESIS ERA EQUIVOCADA
============================================================

Apostaba a la puerta **6d** (el ticker sin pata default en `mercado.especies`,
porque `BPOA7 → BPA7D` pierde una letra del medio y rompe cualquier regla de
string). La corrida dio **6d = 0**.

    el detector CANTA          6    (los 5 BPO + GD46)
    llega al final y NO canta  11   ← todos 6c

Los 11 son **`6c`**: el master **ya apunta a la pata que `especies` marca como
default**, y esa pata igual cotiza en pesos. Así que «completar `especies`» —lo
que este script decía en su primera versión— **no arregla nada**: habría mandado
a corregir lo que no está roto.

La pregunta que queda, y que el script ahora también contesta: **¿existe una
pata en dólares para estos once?**

  · NO existe → el bono cotiza en pesos y punto. No hay nada que arreglar; lo
    discutible es por qué está en una curva USD.
  · SÍ existe → hay un dato mal cargado, y hay arreglo.

**Contestado (2026-08-22): 11 de 11 TIENEN pata en dólares**, todos con la misma
forma —`★ VSCYO ARS 24hs` como default y `VSCYD USD 24hs` al lado—. Eso destapó
que `es_default` es una COPIA de `curvas.instrumento` y que la comparación del
detector era circular (§0.bu). El criterio pasó a ser la MONEDA DEL EJE.
"""
from __future__ import annotations

PARIDAD_MIN, PARIDAD_MAX = 20.0, 400.0


def _sym(simbolo: str) -> str:
    """El símbolo pelado (`AL30D`) del completo (`MERV - XMEV - AL30D - 24hs`).

    Vive acá y no adentro del loop porque la pata en dólares se decide por el
    SUFIJO del símbolo — el mismo criterio que usa el detector. Escribirlo dos
    veces es cómo un día opinan distinto.
    """
    return simbolo.split(" - ")[2] if " - " in simbolo else simbolo


def main() -> int:
    from api.services.macro import get_ultimo_mep
    from core import curvas_sql, market_snapshot
    from core import especies as _esp
    from core.postgres import get_pool

    print("═" * 78)
    print("  LOS QUE COTIZAN EN PESOS Y EL DETECTOR NO CANTA — dónde se caen")
    print("═" * 78)

    try:
        mep = float((get_ultimo_mep() or {}).get("mep") or 0)
    except Exception:
        mep = 0.0
    print(f"\n  MEP usado             {mep:,.2f}")
    if mep <= 0:
        print("\n  ✖ Sin MEP el detector devuelve vacío POR DISEÑO (no inventa).\n")
        return 1

    # Las MISMAS fuentes que arma `av_agent.relevar_live` — si acá se leyeran
    # de otro lado, el diag mediría un universo distinto del que ve el detector
    # y su respuesta no valdría nada.
    bonos = curvas_sql.cargar_todos() or []
    simbolos_master = {(b.get("ticker") or "").strip() for b in bonos}
    snap = market_snapshot.cols_map(simbolos_master, ["last_price"]) or {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT simbolo, ticker, es_default, especie, plazo "
                    "FROM mercado.especies")
        filas = cur.fetchall()
    defaults = {(r[1] or "").strip().upper(): r[0]
                for r in filas if r[2] and r[0] and r[1]}
    patas: dict[str, list[dict]] = {}
    for sim, tk_, _d, esp, plazo in filas:
        if sim and tk_:
            patas.setdefault((tk_ or "").strip().upper(), []).append(
                {"simbolo": sim, "especie": esp, "plazo": plazo})

    # ⚠️⚠️ **EL VEREDICTO LO DA EL DETECTOR DE VERDAD, NO UNA COPIA.**
    #
    # La primera versión de este script reimplementaba las seis puertas —
    # incluida la 6, que es la que decide. Cuando el detector se arregló
    # (§0.bu), **el diag siguió midiendo su propia copia vieja** y siguió
    # diciendo «11 no se cantan» cuando ya se cantaban.
    #
    # O sea que la herramienta que existe para cazar REGLA #9 tenía REGLA #9
    # adentro, y de la peor forma: no falló, contestó con seguridad usando el
    # dato equivocado. La única defensa es que el veredicto salga de la función
    # REAL — la caminata por las puertas queda solo para EXPLICAR dónde cae
    # cada bono, que es lo que un `for` sobre los hallazgos no puede decir.
    from api.services.av_agent import detectar_precio_fuera_de_moneda
    reales: dict[str, str] = {}
    for h in detectar_precio_fuera_de_moneda(
            bonos, snap, mep, set(), defaults, None, patas):
        reales[(h.get("ticker") or "").strip().upper()] = h.get("regla") or ""
    print(f"  bonos en el master    {len(bonos)}")
    print(f"  con pata default      {len(defaults)}")

    motivos: dict[str, list[str]] = {}

    def anota(clave: str, txt: str) -> None:
        motivos.setdefault(clave, []).append(txt)

    for b in bonos:
        tk = (b.get("ticker_corto") or "").strip().upper()
        simbolo = (b.get("ticker") or "").strip()
        if (b.get("moneda_eje") or "").upper() != "USD":
            anota("1_no_es_curva_usd", tk)
            continue
        if "dolar_linked" in {(b.get("ajuste") or "").strip().lower(),
                              (b.get("ajuste_alt") or "").strip().lower(),
                              (b.get("curva") or "").strip().lower()}:
            anota("2_dolar_linked", tk)
            continue
        d = snap.get(simbolo) or {}
        try:
            px = float(d.get("last_price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if px <= 0:
            anota("3_sin_precio", tk)
            continue
        try:
            residual = float(b.get("valor_nominal") or 100) or 100
        except (TypeError, ValueError):
            residual = 100.0
        par_cruda = px / residual * 100
        if PARIDAD_MIN <= par_cruda <= PARIDAD_MAX:
            anota("4_ya_viene_en_dolares", tk)
            continue
        par_mep = px / mep / residual * 100
        if not (PARIDAD_MIN <= par_mep <= PARIDAD_MAX):
            anota("5_dividir_no_lo_arregla",
                  f"{tk} px={px:,.2f} cruda={par_cruda:,.0f}% mep={par_mep:,.1f}%")
            continue

        partes = simbolo.split(" - ")
        sym = partes[2] if len(partes) >= 3 else simbolo
        # Acá NO se decide: se PREGUNTA. `reales` sale de correr el detector.
        regla = reales.get(tk)
        if regla:
            mejor = _esp.pata_para_el_eje(patas.get(tk) or [], b.get("moneda_eje"))
            sug = _sym(mejor["simbolo"]) if mejor else ""
            anota(f"6_CANTA_{regla}", f"{tk} ({sym})" + (f" → {sug}" if sug else ""))
        else:
            anota("6_NO_CANTA", f"{tk} ({sym})")

    print("\n" + "─" * 78)
    print("  DÓNDE SE CAE CADA BONO (en el orden real del detector)")
    print("─" * 78)
    for k in sorted(motivos):
        v = motivos[k]
        print(f"\n  {k}   {len(v)}")
        # Las puertas 1-4 son masivas y correctas: alcanza el conteo. Las de la
        # 5 en adelante son las que hay que leer una por una.
        if k.startswith(("5", "6")):
            for x in sorted(v)[:40]:
                print(f"      {x}")
            if len(v) > 40:
                print(f"      … y {len(v) - 40} más")

    print("\n" + "─" * 78)
    print("  LA RESPUESTA")
    print("─" * 78)
    canta = sum(len(v) for k, v in motivos.items() if k.startswith("6_CANTA_"))
    ciegos = len(motivos.get("6_NO_CANTA", []))
    print(f"\n  el detector CANTA          {canta}")
    print(f"  llega al final y NO canta  {ciegos}   ← estos son los que faltan")
    if ciegos:
        print("\n  Los de `6_NO_CANTA` pasaron TODAS las puertas —o sea: cotizan")
        print("  en pesos de verdad— y el detector se queda callado.")

    # ⚠️⚠️ **LA PRIMERA CORRIDA DESMINTIÓ LA HIPÓTESIS DE ESTE SCRIPT.**
    #
    # El docstring apostaba a `6d` (el ticker sin pata default en
    # `mercado.especies`) y la medición dio **6d = 0**: los 11 que faltan son
    # TODOS `6c`, o sea que el master **ya apunta a la pata que `especies` marca
    # como default** y esa pata igual cotiza en pesos.
    #
    # Entonces «completar `especies`» —lo que este mismo script decía en su
    # primera versión— **no arregla nada acá**, y dejarlo escrito habría mandado
    # a arreglar lo que no está roto. La pregunta que queda es otra y es la de
    # abajo: **¿existe siquiera una pata en dólares para estos?**
    #
    #   · si NO existe  → el bono cotiza en pesos y punto. No hay nada que
    #                     arreglar: lo que hay que revisar es por qué está en
    #                     una curva USD, o aceptar que es contexto y no error.
    #   · si SÍ existe  → `es_default` está eligiendo mal (Primary marca la más
    #                     operada, que no es la que necesita una curva en USD).
    #                     ESE sí es un dato mal cargado, y con arreglo.
    faltan = [x.split(" ")[0] for x in motivos.get("6_NO_CANTA", [])]
    if faltan:
        print("\n" + "─" * 78)
        print("  ¿TIENEN PATA EN DÓLARES? — la pregunta que decide el arreglo")
        print("─" * 78)
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT upper(ticker), simbolo, moneda, plazo, es_default "
                "  FROM mercado.especies WHERE upper(ticker) = ANY(%s) "
                " ORDER BY ticker, es_default DESC, simbolo", (faltan,))
            patas = cur.fetchall()
        por_tk: dict[str, list[tuple]] = {}
        for tk, sim, mon, plazo, dflt in patas:
            por_tk.setdefault(tk, []).append((sim, mon, plazo, dflt))
        con_d, sin_d = [], []
        for tk in sorted(faltan):
            filas = por_tk.get(tk, [])
            dolares = [f for f in filas if _sym(f[0])[-1:].upper() == "D"]
            (con_d if dolares else sin_d).append(tk)
            print(f"\n  {tk}   {len(filas)} pata(s)"
                  f"{'   ← TIENE pata D' if dolares else '   ← sin pata D'}")
            for sim, mon, plazo, dflt in filas:
                print(f"      {'★' if dflt else ' '} {_sym(sim):<16} "
                      f"{(mon or '?'):<5} {(plazo or '?')}")
        print("\n" + "─" * 78)
        print(f"  CON pata D (es_default elige mal → hay arreglo)   {len(con_d)}"
              f"   {', '.join(con_d) or '—'}")
        print(f"  SIN pata D (cotiza en pesos y punto)              {len(sin_d)}"
              f"   {', '.join(sin_d) or '—'}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
