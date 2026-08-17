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
  4. **Avisa de las cruzadas**: bonos cuyo default NO es de su moneda **y que
     tienen una pata de la moneda correcta a la que apuntar**. Sin esa segunda
     condición marcaba 137 falsos positivos: las ONs no tienen pata D (su ticker
     YA termina en O, que es parte del NOMBRE y no un sufijo de especie), así que
     cotizar en su única especie no es un cruce.

**El catálogo de Primary queda VIEJO y eso no invalida nada**: verificado el
2026-08-15, `AO29` no figura en `manager.pyrofex_instruments` y sin embargo
mandado a mano devuelve precio. Por eso la pata que el master usa HOY se siembra
siempre, figure o no — si no, sembrar borraría el símbolo que la vista está
usando. Refrescar el catálogo: `python -m scripts.discovery_pyrofex`.

Uso:
    python -m scripts.sembrar_especies              # DRY-RUN (default)
    python -m scripts.sembrar_especies --aplicar    # escribe mercado.especies
"""
from __future__ import annotations

import argparse

from core import especies as _esp
from core.postgres import get_pool

_RE_ESPECIE = _esp.RE_ESPECIE
# El sufijo del SÍMBOLO manda sobre el label: `engines/curvas.py:223` ya decide la
# moneda así ("el ticker_corto es un label humano y puede no reflejar la moneda").
_ESPECIE = _esp.ESPECIE
_ESPERADA = {"USD": {"mep", "cable"}, "EUR": {"mep", "cable"}, "ARS": {"pesos"}}


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


# Cuál pata proponer cuando el default está cruzado: MEP antes que cable (es la
# que mira la mesa) y 24hs antes que CI (es el plazo estándar de la vista).
def _armar(curvas: list[dict], simbolos: list[str]) -> tuple[list[dict], list[dict], list[str]]:
    """(filas a escribir, cruzadas, fuera del catálogo de Primary). PURO."""
    # El universo para clasificar incluye los símbolos del MASTER: si no, una pata
    # que Primary todavía no lista (catálogo viejo) no puede formar par y se
    # clasifica mal.
    universo = {s[2].upper() for x in simbolos if (s := _esp.segs(x))}
    universo |= {s[2].upper() for c in curvas
                 if (s := _esp.segs((c.get("instrumento") or "").strip()))}
    clasificar = _esp.clasificador(universo)

    univ: dict[str, list[dict]] = {}
    for x in simbolos:
        s = _esp.segs(x)
        if not s:
            continue
        base, esp, mon = clasificar(s[2])
        univ.setdefault(base, []).append({
            "simbolo": x, "ticker": base, "ticker_especie": s[2].upper(),
            "especie": esp, "moneda": mon, "plazo": s[3]})

    filas: list[dict] = []
    cruzadas: list[dict] = []
    fuera: list[str] = []
    for c in curvas:
        tk = (c.get("ticker") or "").strip().upper()
        base, _, _ = clasificar(tk)
        patas = list(univ.get(base) or [])
        actual = (c.get("instrumento") or "").strip()

        # Si el master apunta a un símbolo que Primary NO lista, se REPORTA y NO
        # se siembra. Hubo una rama que lo sembraba igual, por la hipótesis de que
        # el discovery quedaba viejo (`AO29` no figuraba y devolvía precio a mano).
        # El 2026-08-15 se refrescó el discovery y los faltantes quedaron
        # exactamente iguales: no era el catálogo atrasado, esos símbolos NO
        # existen. Sembrarlos metía en `mercado.especies` patas que no cotizan y
        # las recreaba después de que `jobs/validar_instrumentos` las borrara.
        if actual and not any(p["simbolo"] == actual for p in patas):
            fuera.append(tk)
        if not patas:
            continue

        default = next((p for p in patas if p["simbolo"] == actual), None)
        for p in patas:
            filas.append({**p, "es_default": p["simbolo"] == actual})

        # CRUCE = el default no es de la moneda del bono **Y la alternativa EXISTE**.
        # Sin la segunda mitad esto marcaba 137 falsos positivos: las ONs no tienen
        # pata D (su ticker YA termina en O, que es parte del nombre y no un sufijo
        # de especie), así que un hard dollar corporativo cotizando en su única
        # especie no está cruzado — no hay ninguna otra a la que apuntar.
        esperada = _ESPERADA.get((c.get("moneda_eje") or "").upper())
        if default and esperada and default["especie"] not in esperada:
            alt = sorted((p for p in patas if p["especie"] in esperada), key=_esp.preferencia)
            if alt:
                cruzadas.append({"ticker": tk, "curva": c.get("curva"),
                                 "moneda": c.get("moneda_eje"), "usa": default["especie"],
                                 "deberia": sorted({p["especie"] for p in alt}),
                                 "simbolo_ok": alt[0]["simbolo"]})
    return filas, cruzadas, fuera


def _reporte(filas: list[dict], cruzadas: list[dict], fuera: list[str],
             curvas: list[dict]) -> None:
    por_ticker: dict[str, int] = {}
    for f in filas:
        por_ticker[f["ticker"]] = por_ticker.get(f["ticker"], 0) + 1
    reparto: dict[int, int] = {}
    for n in por_ticker.values():
        reparto[n] = reparto.get(n, 0) + 1

    print(f"\n{'=' * 96}\nPATAS ENCONTRADAS\n{'=' * 96}")
    print(f"  bonos del master: {len(curvas)} · con patas: {len(por_ticker)}")
    print(f"  filas a escribir en mercado.especies: {len(filas)}")
    print(f"  bonos por CANTIDAD de patas: {dict(sorted(reparto.items()))}")
    sin_def = sorted({f["ticker"] for f in filas} -
                     {f["ticker"] for f in filas if f["es_default"]})
    print(f"  bonos sin ninguna pata marcada `es_default`: {len(sin_def)}"
          + ("  ← debería ser 0" if sin_def else ""))
    if sin_def:
        print(f"    {', '.join(sin_def[:20])}")

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
        print(f"           debería usar ({'/'.join(c['deberia'])}): {c['simbolo_ok']}")

    print(f"\n{'=' * 96}\nCATÁLOGO DE PRIMARY DESACTUALIZADO ({len(fuera)})\n{'=' * 96}")
    print("  El símbolo que usa el master NO figura en `manager.pyrofex_instruments`,")
    print("  pero SÍ devuelve precio si se lo manda (verificado con AO29). Su pata se")
    print("  siembra igual — el catálogo viejo no le puede ganar a la realidad.")
    print("  NO es un error de datos: es que el discovery quedó atrasado.")
    if fuera:
        print(f"    {', '.join(sorted(fuera)[:30])}"
              + (f"  … (+{len(fuera) - 30})" if len(fuera) > 30 else ""))
        print("\n  Para refrescarlo:  python -m scripts.discovery_pyrofex")


def _corregir(cruzadas: list[dict], pedidos: set[str]) -> int:
    """Repunta `mercado.curvas.instrumento` a la pata de la moneda correcta.

    SOLO los tickers que el user nombra explícitamente — nunca "todos los
    cruzados". Cambiar el instrumento cambia lo que el motor SUSCRIBE, así que
    un bono cuya pata en dólares casi no opere pasaría de mostrar un precio en
    otra escala a no mostrar NINGUNO. Eso se decide caso por caso mirando el
    mercado, no desde un script."""
    aplicables = [c for c in cruzadas if c["ticker"] in pedidos and c["simbolo_ok"]]
    faltantes = pedidos - {c["ticker"] for c in aplicables}
    if faltantes:
        print(f"\n  ⚠ ignorados (no figuran como cruzados): {', '.join(sorted(faltantes))}")
    if not aplicables:
        return 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for c in aplicables:
            cur.execute("UPDATE mercado.curvas SET instrumento = %s WHERE ticker = %s",
                        (c["simbolo_ok"], c["ticker"]))
            print(f"  ✔ {c['ticker']:<8} {c['usa']} → {c['simbolo_ok']}")
            # `es_default` sigue al master: la pata vieja lo pierde, la nueva lo gana.
            cur.execute("UPDATE mercado.especies SET es_default = (simbolo = %s) "
                        "WHERE ticker = %s", (c["simbolo_ok"], c["ticker"]))
    return len(aplicables)


def main() -> None:
    ap = argparse.ArgumentParser(description="Paso 8: sembrar mercado.especies")
    ap.add_argument("--aplicar", action="store_true", help="escribe (default: dry-run)")
    ap.add_argument("--corregir", help="tickers a repuntar a su pata correcta, "
                                       "separados por coma (ej. AO29). Requiere --aplicar")
    args = ap.parse_args()

    print("=" * 96)
    print("PASO 8 — LAS PATAS DE CADA BONO (mercado.especies)")
    print("=" * 96)

    curvas = _q("SELECT ticker, instrumento, curva, moneda_eje FROM mercado.curvas")
    if not curvas:
        print("✗ mercado.curvas vino vacío")
        return
    simbolos = _esp.simbolos_primary()
    if not simbolos:
        print("✗ manager.pyrofex_instruments vino vacío — corré el discovery primero")
        return
    print(f"universo de Primary: {len(simbolos)} símbolos")

    filas, cruzadas, fuera = _armar(curvas, simbolos)
    _reporte(filas, cruzadas, fuera, curvas)

    if not args.aplicar:
        print(f"\n[DRY-RUN] no se escribió nada. Con --aplicar entran {len(filas)} filas.")
        return
    faltan = not _q("SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'mercado' AND table_name = 'especies'")
    if faltan:
        print("\n🛑 NO se escribió nada: falta la tabla mercado.especies.")
        print("   Corré primero:  python -m scripts.apply_schema")
        return
    n = _esp.escribir(filas)
    print(f"\n✅ {n} filas en mercado.especies. La vista NO cambia: nadie la lee todavía.")

    if args.corregir:
        pedidos = {t.strip().upper() for t in args.corregir.split(",") if t.strip()}
        print(f"\n{'=' * 96}\nCORRIGIENDO EL INSTRUMENTO DEL MASTER ({len(pedidos)} pedidos)"
              f"\n{'=' * 96}")
        k = _corregir(cruzadas, pedidos)
        if k:
            print(f"\n✅ {k} corregidos. ESTO SÍ CAMBIA LA VISTA.")
            print("   El precio pasa a leerse de la pata en dólares.")
            print("   El motor toma el símbolo nuevo al arrancar (`cargar_tickers_ordenados`),")
            print("   así que el cambio se ve cuando cron levante los motores; la API refresca")
            print("   su cache del master sola en ≤300s (`core/curvas_sql.py`).")


if __name__ == "__main__":
    main()
