"""scripts/diag_activos.py — cómo están modelados HOY los activos de renta fija.

UN SOLO COMANDO, READ-ONLY. No escribe, no propone, no arregla nada: junta los
números que hacen falta para rediseñar `mercado.curvas` sin romper la vista más
usada de la app.

**El problema de fondo**: hoy `mercado.curvas` guarda TRES entidades distintas en
una sola fila.

    ACTIVO    AL30                          → emisor, ley, vencimiento, calificación
    ESPECIE   MERV - XMEV - AL30D - 24hs    → 1 a N (pesos / MEP / cable)
    CURVA     el cuadro de flujos + ejes    → 1 por activo

Un bono tiene UN cuadro de flujos y VARIAS especies cotizando. Como la fila es
una sola, la especie quedó congelada en el alta — y de ahí salen los tres
síntomas juntos: la tabla muestra `AL30D` como si fuera el ticker, `AO29` marca
~141.430 al lado de bonos en ~90 (agarró la especie en pesos de un hard dollar),
y no se puede ofrecer "verlo en MEP" porque no hay dónde guardarlo.

Encima los nombres están invertidos: la PK `ticker_corto` ES el ticker, y la
columna `ticker` guarda el símbolo de mercado (lo que se le manda a Primary).

Las cinco preguntas que contesta, en orden:

  1. ESTRUCTURA  — qué hay en cada columna y qué forma tiene.
  2. CRUCE       — ¿`portafolio.assets` puede ser el maestro del activo?
  3. DUPLICACIÓN — la ficha está en las dos tablas: ¿coincide o ya divergió?
  4. ESPECIES    — qué especies cotizan por bono y cuál eligió el master.
  5. PESO        — cuánto ocupa el blob `data` que leen TODOS los readers.

Uso:
    python -m scripts.diag_activos
"""
from __future__ import annotations

import re

from core.postgres import get_pool

# Especie al final del ticker: D = dólar MEP (local), C = cable.
_RE_ESPECIE = re.compile(r"^([A-Z]+\d+)([DC])$")
_ESPECIES = {"": "PESOS", "D": "MEP", "C": "CABLE"}

# Qué especie CORRESPONDE según el eje `moneda_eje` del bono. Un bono denominado
# en USD mirado en la especie PESOS muestra el precio de otra escala.
ESPERADA_POR_MONEDA = {"USD": {"MEP", "CABLE"}, "EUR": {"MEP", "CABLE"},
                       "ARS": {"PESOS"}}

# Ficha que está DUPLICADA en las dos tablas → candidata a vivir en una sola.
_FICHA = [("emisor", "emisor"), ("fecha_vencimiento", "vencimiento")]

_SEP = "─" * 96


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _columnas(schema: str, tabla: str) -> list[str]:
    """Las columnas que EXISTEN de verdad. El schema.sql no siempre está aplicado
    (ver "Capa SQL" en CLAUDE.md), así que preguntar es más barato que fallar."""
    return [r["column_name"] for r in _q(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (schema, tabla))]


def _base_y_especie(tc: str) -> tuple[str, str]:
    """`AL30D` → ('AL30', 'D'). `TX26` → ('TX26', '')."""
    m = _RE_ESPECIE.match((tc or "").strip().upper())
    return (m.group(1), m.group(2)) if m else ((tc or "").strip().upper(), "")


def _segmentos(simbolo: str) -> list[str] | None:
    """'MERV - XMEV - AL30D - 24hs' → los 4 segmentos, o None si no tiene esa forma.

    Por SPLIT y no por regex: un `^(.*?-\\s*)([A-Z0-9]+)(\\s*-.*)$` parece que aísla
    el ticker y en realidad captura `XMEV` (el mercado), porque el no-greedy corta
    en el primer guión. Lo destapó un smoke con datos falsos — de otro modo el
    diag habría reportado "ninguna especie" para TODO y el número habría pasado
    por bueno.
    """
    segs = [x.strip() for x in (simbolo or "").split(" - ")]
    return segs if len(segs) >= 3 else None


def _norm_fecha(v) -> str:
    return str(v)[:10] if v else ""


def _normalizado(curvas: list[dict]) -> list[dict]:
    """Copias con el vocabulario NUEVO: `ticker` = AL30, `instrumento` = el
    símbolo de mercado. El renombre del 2026-08-15 los invirtió respecto de cómo
    se llamaban (`ticker_corto` / `ticker`), y este diag tiene que correr igual
    de los dos lados — si solo anduviera después del deploy no serviría para
    decidir el deploy. El bloque 1 NO usa esto: ahí se reportan las columnas
    crudas, que es justamente lo que se quiere ver."""
    out = []
    for d in curvas:
        c = dict(d)
        if "ticker_corto" in c:                      # esquema VIEJO
            c["instrumento"] = c.get("ticker")
            c["ticker"] = c.pop("ticker_corto")
        out.append(c)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 1) ESTRUCTURA
# ─────────────────────────────────────────────────────────────────────────────
def _bloque_estructura(curvas: list[dict], cols: list[str]) -> None:
    print("\n" + "=" * 96)
    print("1) ESTRUCTURA de mercado.curvas — qué hay REALMENTE en cada columna")
    print("=" * 96)
    print(f"   filas: {len(curvas)} · columnas: {len(cols)}")

    llenas = []
    for c in cols:
        if c in ("data", "flujos"):
            continue
        n = sum(1 for d in curvas if d.get(c) not in (None, ""))
        llenas.append((c, n))
    print(f"\n   {'COLUMNA':<20}{'CON DATO':>10}   MUESTRA")
    print("   " + _SEP[:93])
    for c, n in llenas:
        ej = next((str(d[c]) for d in curvas if d.get(c) not in (None, "")), "")
        vacio = "  ← VACÍA" if n == 0 else ""
        print(f"   {c:<20}{n:>10}   {ej[:52]}{vacio}")

    # La forma del símbolo de mercado: ¿son todos MERCADO - SEGMENTO - TICKER - PLAZO?
    simb = "instrumento" if "ticker_corto" not in cols else "ticker"
    con_forma = sum(1 for d in curvas if _segmentos(d.get(simb) or ""))
    print(f"\n   `{simb}` con forma 'MERV - XMEV - XXX - 24hs': {con_forma} de {len(curvas)}")
    print("     → esa columna NO es un ticker: es el SÍMBOLO DE MERCADO (lo que se")
    print("       le manda a Primary). El ticker de verdad es la PK de la tabla.")

    # Los plazos y mercados que aparecen — para saber si la especie es 1:N real.
    plazos: dict[str, int] = {}
    mercados: dict[str, int] = {}
    for d in curvas:
        s = _segmentos(d.get(simb) or "")
        if s:
            plazos[s[-1]] = plazos.get(s[-1], 0) + 1
            mercados[f"{s[0]}/{s[1]}"] = mercados.get(f"{s[0]}/{s[1]}", 0) + 1
    print(f"   plazos en uso: {dict(sorted(plazos.items(), key=lambda x: -x[1]))}")
    print(f"   mercados:      {dict(sorted(mercados.items(), key=lambda x: -x[1]))}")

    sufijos: dict[str, int] = {}
    for d in curvas:
        tk = d.get("ticker_corto") if "ticker_corto" in cols else d.get("ticker")
        _, e = _base_y_especie(tk or "")
        sufijos[_ESPECIES.get(e, e)] = sufijos.get(_ESPECIES.get(e, e), 0) + 1
    pk = "ticker_corto" if "ticker_corto" in cols else "ticker"
    print(f"\n   La PK `{pk}`, ¿trae la ESPECIE pegada? {sufijos}")
    print("     → un ticker con sufijo D/C no es un ticker: es el bono + la moneda")
    print("       en la que se lo mira. El bono y su cuadro de flujos son el MISMO.")


# ─────────────────────────────────────────────────────────────────────────────
# 2) CRUCE con portafolio.assets
# ─────────────────────────────────────────────────────────────────────────────
def _bloque_cruce(curvas: list[dict], assets: list[dict]) -> list[tuple[dict, dict]]:
    print("\n" + "=" * 96)
    print("2) CRUCE mercado.curvas ↔ portafolio.assets — ¿assets puede ser el maestro?")
    print("=" * 96)

    por_ticker: dict[str, list[dict]] = {}
    for a in assets:
        t = (a.get("ticker") or "").strip().upper()
        if t:
            por_ticker.setdefault(t, []).append(a)

    pares: list[tuple[dict, dict]] = []
    huerfanos, por_base = [], []
    for d in curvas:
        tc = (d.get("ticker") or "").strip().upper()
        base, esp = _base_y_especie(tc)
        if tc in por_ticker:
            pares.append((d, por_ticker[tc][0]))
        elif esp and base in por_ticker:
            # El master usa AL30D y assets tiene AL30: el activo SÍ está, lo que
            # no matchea es la especie pegada al ticker. Es el bug, no un faltante.
            por_base.append((tc, base))
            pares.append((d, por_ticker[base][0]))
        else:
            huerfanos.append(tc)

    print(f"   instrumentos en curvas: {len(curvas)} · assets con ticker: {len(por_ticker)}")
    print(f"   ✓ matchean directo por ticker:            {len(pares) - len(por_base)}")
    print(f"   ~ matchean SOLO sacando la especie (D/C): {len(por_base)}")
    if por_base:
        print("       " + ", ".join(f"{a}→{b}" for a, b in por_base[:18]))
        print("       → el activo YA está en assets; lo que no cruza es el sufijo.")
    print(f"   ✗ sin fila en assets:                     {len(huerfanos)}")
    if huerfanos:
        print("       " + ", ".join(huerfanos[:25]))
        print("       → sin esto no aparecen en AuM/Portfolios (join chain de CLAUDE.md).")

    dupes = {t: len(v) for t, v in por_ticker.items() if len(v) > 1}
    print(f"\n   Tickers con MÁS DE UN asset (varias `unidad`): {len(dupes)}")
    if dupes:
        muestra = dict(sorted(dupes.items(), key=lambda x: -x[1])[:10])
        print(f"       {muestra}")
        print("       → si esto es alto, `ticker` NO alcanza como clave del activo.")
    return pares


# ─────────────────────────────────────────────────────────────────────────────
# 3) DUPLICACIÓN de la ficha
# ─────────────────────────────────────────────────────────────────────────────
def _bloque_duplicacion(pares: list[tuple[dict, dict]]) -> None:
    print("\n" + "=" * 96)
    print("3) DUPLICACIÓN — la misma ficha en las dos tablas: ¿coincide o ya divergió?")
    print("=" * 96)
    if not pares:
        print("   (sin pares para comparar)")
        return

    print(f"   {'CAMPO':<22}{'AMBAS':>8}{'IGUAL':>8}{'DISTINTO':>10}{'SOLO CURVAS':>13}"
          f"{'SOLO ASSETS':>13}")
    print("   " + _SEP[:93])
    ejemplos: dict[str, list[str]] = {}
    for cc, ca in _FICHA:
        ambas = igual = distinto = solo_c = solo_a = 0
        for c, a in pares:
            vc, va = c.get(cc), a.get(ca)
            if cc.startswith("fecha") or ca == "vencimiento":
                vc, va = _norm_fecha(vc), _norm_fecha(va)
            vc = (str(vc).strip() if vc not in (None, "") else "")
            va = (str(va).strip() if va not in (None, "") else "")
            if vc and va:
                ambas += 1
                if vc.upper() == va.upper():
                    igual += 1
                else:
                    distinto += 1
                    ejemplos.setdefault(f"{cc}/{ca}", []).append(
                        f"{c.get('ticker')}: curvas={vc[:22]!r} assets={va[:22]!r}")
            elif vc:
                solo_c += 1
            elif va:
                solo_a += 1
        print(f"   {cc + ' / ' + ca:<22}{ambas:>8}{igual:>8}{distinto:>10}"
              f"{solo_c:>13}{solo_a:>13}")
    for k, v in ejemplos.items():
        print(f"\n   DIVERGENCIAS en {k} (primeras 8):")
        for x in v[:8]:
            print(f"       {x}")
    print("\n   → 'DISTINTO' > 0 es la prueba de que duplicar la ficha ya creó DOS")
    print("     verdades. Lo mismo que pasó con el rebautizo de especies de Aunesa.")


# ─────────────────────────────────────────────────────────────────────────────
# 4) ESPECIES
# ─────────────────────────────────────────────────────────────────────────────
def _bloque_especies(curvas: list[dict], snap: dict[str, dict]) -> None:
    print("\n" + "=" * 96)
    print("4) ESPECIES — qué cotiza de verdad por bono y cuál eligió el master")
    print("=" * 96)

    filas = []
    for d in curvas:
        base, esp = _base_y_especie(d.get("ticker") or "")
        segs = _segmentos(d.get("instrumento") or "")
        disp: dict[str, float | None] = {}
        if segs:
            for suf, nombre in _ESPECIES.items():
                cand = " - ".join(segs[:2] + [base + suf] + segs[3:])
                if cand in snap:
                    disp[nombre] = snap[cand].get("last_price")
        elegida = _ESPECIES.get(esp, esp)
        px = disp.get(elegida)
        # NO se juzga por magnitud. Un "100x contra las hermanas" marca a AO29D
        # (que usa MEP y está BIEN) igual que a un error real: un hard dollar
        # SIEMPRE tiene una hermana en pesos ~1.400x, esté bien o mal elegida.
        # El criterio correcto ya está clasificado en el eje `moneda_eje`: si el
        # bono se denomina en USD, la especie tiene que ser MEP o CABLE.
        esperadas = ESPERADA_POR_MONEDA.get((d.get("moneda_eje") or "").upper())
        cruzada = bool(esperadas and elegida not in esperadas
                       and any(k in disp for k in esperadas))
        filas.append({"base": base, "tc": d.get("ticker"), "curva": d.get("curva"),
                      "moneda": d.get("moneda_eje"), "usa": elegida, "px": px,
                      "disp": disp, "cruzada": cruzada})

    multi = [f for f in filas if len(f["disp"]) > 1]
    cruz = [f for f in filas if f["cruzada"]]
    sin_snap = [f for f in filas if not f["disp"]]
    sin_eje = [f for f in filas if not f["moneda"]]

    print(f"   Bonos con MÁS DE UNA especie cotizando: {len(multi)} de {len(filas)}")
    print("     → hoy el master elige UNA y la congela: no se puede ver el mismo bono")
    print("       en pesos/MEP/cable sin dar de alta otro instrumento.")
    print(f"   ⚠ MONEDA CRUZADA (el eje dice una cosa y la especie otra): {len(cruz)}")
    print(f"   Sin ninguna especie en el snapshot: {len(sin_snap)}")
    if sin_eje:
        print(f"   Sin `moneda_eje` (no se puede juzgar la especie): {len(sin_eje)}")

    if cruz:
        print(f"\n   ⚠ MONEDA CRUZADA — el precio es de otra escala ({len(cruz)}):")
        print(f"   {'TICKER':<10}{'CURVA':<14}{'EJE':<6}{'USA':<7}{'PRECIO':>13}   ESPECIES")
        print("   " + _SEP[:93])
        for f in sorted(cruz, key=lambda x: x["base"]):
            d = " · ".join(f"{k}={v:,.2f}" for k, v in sorted(f["disp"].items()) if v)
            p = f"{f['px']:,.2f}" if f["px"] else "--"
            print(f"   {str(f['tc'])[:10]:<10}{str(f['curva'] or '')[:13]:<14}"
                  f"{str(f['moneda'] or '')[:5]:<6}{f['usa']:<7}{p:>13}   {d}")

    if multi:
        print(f"\n   MULTI-ESPECIE — los que podrían mirarse en varias monedas ({len(multi)}):")
        print(f"   {'TICKER':<10}{'USA':<7}   ESPECIES EN EL SNAPSHOT")
        print("   " + _SEP[:93])
        for f in sorted(multi, key=lambda x: x["base"])[:40]:
            d = " · ".join(f"{k}={v:,.2f}" if v else f"{k}=--"
                          for k, v in sorted(f["disp"].items()))
            print(f"   {str(f['tc'])[:10]:<10}{f['usa']:<7}   {d}")
        if len(multi) > 40:
            print(f"   … y {len(multi) - 40} más")

    if sin_snap:
        print(f"\n   SIN COTIZAR hoy ({len(sin_snap)}): "
              + ", ".join(str(f["tc"]) for f in sin_snap[:30]))


# ─────────────────────────────────────────────────────────────────────────────
# 5) PESO
# ─────────────────────────────────────────────────────────────────────────────
def _bloque_peso() -> None:
    print("\n" + "=" * 96)
    print("5) PESO — cuánto cuesta el blob `data` que leen TODOS los readers")
    print("=" * 96)
    try:
        r = _q("SELECT count(*) AS n, "
               "coalesce(sum(pg_column_size(data)), 0) AS data_b, "
               "coalesce(sum(pg_column_size(flujos)), 0) AS flujos_b, "
               "coalesce(sum(pg_column_size(c.*)), 0) AS total_b "
               "FROM mercado.curvas c")[0]
    except Exception as e:
        print(f"   no se pudo medir: {e}")
        return
    kb = lambda b: f"{(b or 0) / 1024:,.0f} KB"              # noqa: E731
    print(f"   filas: {r['n']}")
    print(f"   `data`   (doc completo duplicado): {kb(r['data_b'])}")
    print(f"   `flujos` (cronograma):             {kb(r['flujos_b'])}")
    print(f"   TOTAL de la tabla:                 {kb(r['total_b'])}")
    if r["total_b"]:
        print(f"   → `data` es el {100 * (r['data_b'] or 0) / r['total_b']:.0f}% de la tabla.")
    print("   core/curvas_sql.py:31 hace `SELECT data FROM mercado.curvas` y lo cachea")
    print("   300s: CADA lectura del master deserializa esos KB aunque use 5 campos.")
    print("   Ese blob es la copia #3 de la ficha (assets + columnas tipadas + data).")


def main() -> None:
    cols_c = _columnas("mercado", "curvas")
    if not cols_c:
        print("mercado.curvas no existe o no es visible.")
        return
    pedir = [c for c in cols_c if c not in ("data", "flujos")]
    curvas = _q(f"SELECT {', '.join(pedir)} FROM mercado.curvas")

    cols_a = _columnas("portafolio", "assets")
    assets = _q(f"SELECT {', '.join(c for c in cols_a if c != 'data')} "
                "FROM portafolio.assets") if cols_a else []

    snap = {r["ticker"]: r for r in _q(
        "SELECT ticker, last_price FROM mercado.market_snapshot")}

    print("=" * 96)
    print("DIAG ACTIVOS — modelado de renta fija (READ-ONLY, no escribe nada)")
    print("=" * 96)
    print(f"mercado.curvas: {len(curvas)} · portafolio.assets: {len(assets)} · "
          f"market_snapshot: {len(snap)}")

    _bloque_estructura(curvas, cols_c)
    norm = _normalizado(curvas)
    pares = _bloque_cruce(norm, assets)
    _bloque_duplicacion(pares)
    _bloque_especies(norm, snap)
    _bloque_peso()

    print("\n" + "=" * 96)
    print("Nada de esto se corrigió acá. Es el relevamiento para decidir el modelo.")
    print("=" * 96)


if __name__ == "__main__":
    main()
