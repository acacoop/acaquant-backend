"""scripts/diag_duales_1816.py — DISCOVERY de los duales: qué tenemos y qué tiene 1816.

**El problema, en palabras de la mesa:** un dual NO rinde lo mismo según la curva
por la que se lo mire. Un TAMAR+FIJA leído como tasa fija rinde X; leído como
TAMAR rinde Y. Hoy el sistema lo muestra en sus DOS tablas pero con **la MISMA
TEA en las dos**, porque el motor calcula una sola tasa con un solo cronograma.
Eso es literalmente mostrar dos veces el mismo número y llamarlo análisis.

**Por qué no alcanza con "traer el otro flujo".** Un dual paga el MÁXIMO entre dos
esquemas. La pata FIJA está determinada (el cupón se conoce hoy). La otra pata
—TAMAR, CER, devaluación— **depende de un futuro que nadie sabe**: no es un dato
que se busca, es una PROYECCIÓN con un supuesto adentro. Así que la pregunta real
de este discovery no es "¿1816 tiene el otro cashflow?" sino:

    ¿1816 publica UN cronograma o DOS? Y si publica uno, ¿bajo qué supuesto?

Si publica uno solo, la segunda tasa la vamos a tener que **calcular nosotros**, y
entonces el supuesto (qué TAMAR/CER se proyecta) es una decisión de mesa que hay
que hacer explícita en pantalla — no esconderla adentro de un número.

**Qué hace este script**, en tres bloques:

  1. **LO QUE TENEMOS** — por cada dual: sus ejes, su cronograma actual (formato y
     cuántos flujos), `tasa_referencia`, `cer_emision`, y la TEA que muestra hoy.
     Sale de nuestra base, gratis.
  2. **LO QUE 1816 YA NOS DIO** — su ficha en `research.mkt_1816_instrumentos`
     (la llena `jobs.mercado_1816_discovery --catalogo`). También gratis: ya está
     en nuestra base.
  3. **EL CASHFLOW DE 1816** — el crudo, campo por campo, SIN interpretar. Esto
     **CUESTA CRÉDITOS** (1 por cupón) → NO corre sin `--pedir`, y antes muestra
     el saldo y el costo estimado.

READ-ONLY sobre nuestra base. Con `--pedir` hace llamadas a 1816 (que consumen
créditos) pero **tampoco escribe**: es un discovery, no una ingesta.

Uso:
    python -m scripts.diag_duales_1816                  # gratis: 1 y 2
    python -m scripts.diag_duales_1816 --pedir          # + cashflow de TODOS
    python -m scripts.diag_duales_1816 --pedir TTD26    # + cashflow de UNO
"""
from __future__ import annotations

import json
import sys

from core.postgres import get_pool

_SEP = "=" * 100


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _shape(flujos: list[dict]) -> str:
    if not flujos:
        return "sin flujos"
    claves: set[str] = set()
    for f in flujos:
        claves |= set(f.keys())
    pct = bool(claves & {"amortizacion_pct", "cupon_sobre_residual"})
    absol = bool(claves & {"amortizacion", "interes"})
    if pct and absol:
        return "mixto"
    return "porcentual" if pct else ("absoluto" if absol else "?")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pedir = "--pedir" in sys.argv

    print(_SEP)
    print("DUALES — qué tenemos nosotros y qué tiene 1816")
    print(_SEP)

    duales = _q("""
        SELECT ticker, curva, emisor, emisor_tipo, moneda_eje, ajuste, ajuste_alt,
               fecha_vencimiento, flujo_vencimiento, data
        FROM mercado.curvas
        WHERE ajuste_alt IS NOT NULL
        ORDER BY fecha_vencimiento, ticker
    """)
    if args:
        duales = [d for d in duales if d["ticker"] in {a.upper() for a in args}]
    if not duales:
        print("\n  No hay duales (`ajuste_alt IS NOT NULL`). Nada que descubrir.")
        return

    # ── 1. LO QUE TENEMOS ───────────────────────────────────────────────────
    simbolos = {d["ticker"]: (d.get("data") or {}).get("ticker") for d in duales}
    snap = {r["ticker"]: r for r in _q(
        "SELECT ticker, last_price, tea, duration FROM mercado.market_snapshot "
        "WHERE ticker = ANY(%s)",
        ([s for s in simbolos.values() if s],))}

    print(f"\n  {len(duales)} duales\n")
    print(f"  {'TICKER':<9}{'PATAS':<22}{'VTO':<12}{'FLUJOS':<9}{'FORMATO':<12}"
          f"{'TASA_REF':<12}{'TEA hoy':>9}")
    print("  " + "-" * 96)
    for d in duales:
        doc = d.get("data") or {}
        flujos = doc.get("flujos") or []
        m = snap.get(simbolos.get(d["ticker"]) or "") or {}
        tea = f"{float(m['tea']) * 100:.2f}%" if m.get("tea") is not None else "--"
        patas = f"{d['ajuste']} + {d['ajuste_alt']}"
        n_fl = str(len(flujos)) if flujos else ("bullet" if doc.get("flujo_vencimiento") else "0")
        print(f"  {str(d['ticker'])[:8]:<9}{patas[:21]:<22}"
              f"{str(d['fecha_vencimiento'] or '—')[:10]:<12}{n_fl:<9}"
              f"{_shape(flujos):<12}{str(doc.get('tasa_referencia') or '—')[:11]:<12}{tea:>9}")

    print("\n  ⚠️ Esa TEA es UNA SOLA para las DOS tablas — es exactamente el problema.")
    print("     Un cronograma → una tasa. Para dos tasas hacen falta dos cronogramas,")
    print("     y el segundo (la pata variable) NO es un dato: es una proyección.")

    # ── 2. LO QUE 1816 YA NOS DIO (gratis, ya está en nuestra base) ─────────
    print(f"\n{_SEP}\n  2 · LO QUE 1816 YA NOS DIO (`research.mkt_1816_instrumentos`, gratis)\n{_SEP}")
    tks = [d["ticker"] for d in duales]
    fichas = _q("SELECT * FROM research.mkt_1816_instrumentos "
                "WHERE upper(ticker) = ANY(%s)", ([t.upper() for t in tks],))
    if not fichas:
        print("\n  ⚠️ NINGUNO de los duales está en el catálogo de 1816.")
        print("     Llenarlo: `python -m jobs.mercado_1816_discovery --apply --catalogo`")
        print("     (recorre las 28 curvas, 1 crédito por curva).")
    else:
        print(f"\n  {len(fichas)}/{len(duales)} tienen ficha. Campos NO vacíos por bono:\n")
        for f in fichas:
            llenos = {k: v for k, v in f.items() if v not in (None, "", [], {})}
            print(f"  ── {f.get('ticker')}")
            for k, v in sorted(llenos.items()):
                print(f"       {k:<22}{str(v)[:66]}")
        faltan = {t.upper() for t in tks} - {str(f.get("ticker") or "").upper() for f in fichas}
        if faltan:
            print(f"\n  Sin ficha: {', '.join(sorted(faltan))}")
    print("\n  👉 Lo que hay que buscar acá: ¿algún campo distingue las DOS PATAS?")
    print("     (una tasa fija Y una referencia variable, o dos cronogramas).")

    # ── 3. EL CASHFLOW DE 1816 — CUESTA CRÉDITOS ────────────────────────────
    print(f"\n{_SEP}\n  3 · EL CASHFLOW DE 1816 (crudo)\n{_SEP}")
    from core import mercado_1816

    if not mercado_1816.disponible():
        print("\n  ⚠️ 1816 no está configurado en este entorno (falta la API key).")
        return

    try:
        bal = mercado_1816.balance()
        dia, mes = bal.get("daily") or {}, bal.get("monthly") or {}
        print(f"\n  CRÉDITOS — día {dia.get('used')}/{dia.get('limit')} · "
              f"mes {mes.get('used')}/{mes.get('limit')}")
    except Exception as e:
        print(f"\n  ⚠️ no se pudo leer el saldo de créditos: {e}")

    print(f"  COSTO: 1 crédito POR CUPÓN. {len(duales)} duales × ~4-20 cupones c/u.")
    if not pedir:
        print("\n  🔒 NO se pidió nada. Para traerlo:")
        print("       python -m scripts.diag_duales_1816 --pedir          (todos)")
        print("       python -m scripts.diag_duales_1816 --pedir TTD26    (uno solo)")
        print("\n  Recomendación: arrancá por UNO. Con un cashflow crudo ya se ve si")
        print("  1816 publica una pata o las dos — y eso decide todo el diseño.")
        print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")
        return

    for d in duales:
        tk = d["ticker"]
        print(f"\n  ── {tk}  ({d['ajuste']} + {d['ajuste_alt']}) " + "─" * 40)
        try:
            resp = mercado_1816.cashflow(tk)
        except Exception as e:
            print(f"     ❌ {type(e).__name__}: {str(e)[:140]}")
            continue
        cf = resp.get("cashflow") or []
        # Las claves de NIVEL SUPERIOR son lo que más importa: si 1816 distinguiera
        # las dos patas, la distinción viviría acá (o en un cupón con dos montos).
        top = {k: v for k, v in resp.items() if k != "cashflow"}
        print(f"     claves top-level : {json.dumps(top, ensure_ascii=False, default=str)[:180]}")
        print(f"     cupones          : {len(cf)}")
        if cf:
            claves = sorted({k for c in cf for k in c})
            print(f"     claves por cupón : {', '.join(claves)}")
            print("     primeros 3 cupones, CRUDOS (sin interpretar):")
            for c in cf[:3]:
                print(f"       {json.dumps(c, ensure_ascii=False, default=str)[:150]}")

    print(f"\n{_SEP}")
    print("  CÓMO LEERLO")
    print(_SEP)
    print("  · Si aparece UN solo `flujoTotal` por fecha → 1816 publica UNA pata.")
    print("    La segunda tasa la calculamos nosotros, y hay que decidir (y MOSTRAR)")
    print("    con qué TAMAR/CER se proyecta. Ese supuesto no puede quedar escondido")
    print("    adentro de un número: dos mesas con supuestos distintos verían la")
    print("    misma pantalla y no sabrían por qué no coinciden.")
    print("  · Si aparecen DOS montos por fecha (o dos bloques) → 1816 ya resuelve")
    print("    las dos patas y el trabajo es de ingesta, no de modelo.")
    print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")


if __name__ == "__main__":
    main()
