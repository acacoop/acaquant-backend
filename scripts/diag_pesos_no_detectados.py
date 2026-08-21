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
  6. sin pata default         **acá es donde se cae el que buscamos**

La puerta 6 es la sospechosa (hipótesis, sin medir): `defaults` sale de
`mercado.especies.es_default`, y si un ticker no tiene fila ahí el detector
**no puede nombrar la pata correcta** y por eso no emite `pata_equivocada`.
`BPOA7 → BPA7D` pierde una letra del medio, que es exactamente el caso que
REGLA #9 dice que rompe cualquier regla de string.

Si la medición confirma eso, el arreglo NO es tocar el detector: es completar
`mercado.especies` (o emparejar con `core/pareo.hermanas`).
"""
from __future__ import annotations

PARIDAD_MIN, PARIDAD_MAX = 20.0, 400.0


def main() -> int:
    from core import curvas_sql, market_snapshot
    from core.dolar_sql import get_ultimo_mep
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
        cur.execute("SELECT simbolo, ticker, es_default FROM mercado.especies")
        filas = cur.fetchall()
    defaults = {(r[1] or "").strip().upper(): r[0]
                for r in filas if r[2] and r[0] and r[1]}
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
        if sym[-1:].upper() in ("D", "C"):
            anota("6a_CANTA_precio_fuera_de_escala", f"{tk} ({sym})")
            continue
        default = defaults.get(tk, "")
        if default and default != simbolo:
            anota("6b_CANTA_pata_equivocada", f"{tk} → {default.split(' - ')[2]}")
        elif default:
            # Ya apunta a la default y AUN ASÍ el precio viene en pesos: la
            # default de `especies` puede estar mal, o Primary lista la pata en
            # pesos como la que más opera. Es un caso distinto y no está cubierto.
            anota("6c_APUNTA_A_LA_DEFAULT_Y_ES_EN_PESOS", f"{tk} ({sym})")
        else:
            anota("6d_SIN_PATA_DEFAULT_no_puede_nombrarla", f"{tk} ({sym})")

    print("\n" + "─" * 78)
    print("  DÓNDE SE CAE CADA BONO (en el orden real del detector)")
    print("─" * 78)
    for k in sorted(motivos):
        v = motivos[k]
        print(f"\n  {k}   {len(v)}")
        # Las puertas 1-4 son masivas y correctas: alcanza el conteo. Las de la
        # 5 en adelante son las que hay que leer una por una.
        if k[0] in "56":
            for x in sorted(v)[:40]:
                print(f"      {x}")
            if len(v) > 40:
                print(f"      … y {len(v) - 40} más")

    print("\n" + "─" * 78)
    print("  LA RESPUESTA")
    print("─" * 78)
    canta = len(motivos.get("6a_CANTA_precio_fuera_de_escala", [])) \
        + len(motivos.get("6b_CANTA_pata_equivocada", []))
    ciegos = len(motivos.get("6c_APUNTA_A_LA_DEFAULT_Y_ES_EN_PESOS", [])) \
        + len(motivos.get("6d_SIN_PATA_DEFAULT_no_puede_nombrarla", []))
    print(f"\n  el detector CANTA          {canta}")
    print(f"  llega al final y NO canta  {ciegos}   ← estos son los que faltan")
    if ciegos:
        print("\n  Los 6c/6d pasaron TODAS las puertas —o sea: cotizan en pesos de")
        print("  verdad— y el detector se queda callado porque no sabe qué pata")
        print("  nombrar. Ese es el agujero, y NO se arregla en el detector: se")
        print("  arregla completando `mercado.especies` para esos tickers.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
