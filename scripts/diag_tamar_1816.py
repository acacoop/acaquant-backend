"""scripts/diag_tamar_1816.py — los VALORES de 1816 para nuestros TAMAR y duales.

**Decisión tomada (2026-08-16):** los TAMAR no los vamos a valuar nosotros — se
traen de 1816. El motivo no es pereza: la planilla de la mesa y el header de 1816
dan **el mismo número** (TXMD9 → TEA 38,55% vs 38,62%; spread 9,71% vs 9,73%; el
MISMO precio 84,10). O sea que la mesa ya valida contra 1816. Reimplementar la
metodología nos pondría a competir contra el número que ellos ya miran.

Hoy los TAMAR **no tienen ni cálculo**: caen en el `else` del motor (solo
duration). No están mal valuados — están sin valuar.

**Lo que ya quedó VERIFICADO en la corrida del 2026-08-16:**

  · Las variantes están en nuestro catálogo con grafía **`TICKER @PATA`** (con
    espacio): `TXMD9 @TAMAR`, `TXMD9 @CER`, `TTD26 @TASA FIJA`, `TMVE8 @USD-L`.
  · De los campos candidatos, **`spread` es VÁLIDO** (`margen`/`margin`/
    `spreadTamar`/`margenTamar` devuelven HTTP 400). Los 6 buenos son
    `tea · tna · precioClean · duration · paridad · spread`.

**⚠️ LA TRAMPA QUE NOS COMIMOS**: `indicadores` sin `fechaOperacion` usa **HOY**,
y el 2026-08-16 era DOMINGO → los 6 campos válidos volvieron `null`. No era el
campo: era el día. Está avisado en el docstring de `core.mercado_1816.indicadores`
("corrida de sábado con 0 datos"). Por eso este script **siempre** manda una
fecha explícita y, si vuelve vacía, retrocede día hábil por día hábil.

**Lo que este diag responde ahora**, que es lo último que falta para el job:

  1. ¿Qué VALORES devuelve 1816 para nuestros 18 bonos con pata TAMAR? ¿Trae
     `spread` también para los CORPORATIVOS, o solo para los soberanos?
  2. ¿Las DOS PATAS de un dual rinden distinto? (`TXMD9 @CER` vs `TXMD9 @TAMAR`).
     Si dan distinto, el problema de los duales se resuelve con la MISMA ingesta.
  3. ¿Hay duales que 1816 parte en dos y nosotros NO tenemos marcados como dual?

Costo: ~23 tickers × 6 campos ≈ **140 créditos** de los 100.000 diarios.

READ-ONLY: no escribe en la base. El bloque que gasta créditos NO corre sin
`--pedir`.

Uso:
    python -m scripts.diag_tamar_1816                      # gratis: el universo
    python -m scripts.diag_tamar_1816 --pedir              # + los valores de 1816
    python -m scripts.diag_tamar_1816 --pedir --fecha 2026-08-14
"""
from __future__ import annotations

import datetime as dt
import json
import sys

from core.postgres import get_pool

_SEP = "=" * 100

# Los 6 campos que la API ACEPTA (verificado 2026-08-16 probándolos de a uno:
# `margen`, `margin`, `spreadTamar` y `margenTamar` devuelven HTTP 400).
# `spread` es el candidato a ser el «Margen» que muestra el header de 1816 —
# lo confirma el VALOR, no el hecho de que el campo exista.
_CAMPOS = ["tea", "tna", "precioClean", "duration", "paridad", "spread"]

# Cuántos días hábiles hacia atrás probar si la fecha pedida vuelve vacía.
_MAX_RETROCESO = 5


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _habil_anterior(d: dt.date) -> dt.date:
    """Día hábil ANTERIOR a `d` (solo fines de semana; los feriados los resuelve
    el retroceso del bloque 2 cuando la respuesta vuelve vacía)."""
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def _fmt(v) -> str:
    """4 decimales A PROPÓSITO: todavía no sabemos en qué ESCALA viene `spread`.
    Con 2 decimales, un 0,0973 (fracción) se imprimiría `0,10` y parecería un
    número roto en vez de "el mismo 9,73% expresado en tantos por uno"."""
    if v is None:
        return "--"
    try:
        return f"{float(v):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(v)[:10]


def main() -> None:
    pedir = "--pedir" in sys.argv
    fecha = None
    if "--fecha" in sys.argv:
        fecha = sys.argv[sys.argv.index("--fecha") + 1]

    print(_SEP)
    print("TAMAR + DUALES — qué valores publica 1816")
    print(_SEP)

    # ── 1. NUESTRO universo (GRATIS) ────────────────────────────────────────
    tamar = _q("""
        SELECT ticker, ajuste, ajuste_alt, fecha_vencimiento, emisor_tipo
        FROM mercado.curvas
        WHERE ajuste = 'tamar' OR ajuste_alt = 'tamar'
        ORDER BY fecha_vencimiento, ticker
    """)
    print(f"\n  1 · NUESTRO UNIVERSO — {len(tamar)} bonos con pata TAMAR\n")

    # El catálogo de 1816 es quien manda la GRAFÍA: en vez de construir
    # "TICKER @TAMAR" a mano (y errarle a un espacio), se buscan las variantes
    # que 1816 YA publica para cada uno de nuestros tickers. Si el catálogo no
    # tiene variante, se pide el ticker pelado — que es lo correcto para los
    # TAMAR puros (no son duales, no hay dos patas que separar).
    cat = _q("""
        SELECT ticker, denominacion FROM research.mkt_1816_instrumentos
        WHERE ticker ILIKE '%%@%%'
    """)
    por_base: dict[str, list[str]] = {}
    for c in cat:
        base = str(c["ticker"]).split("@")[0].strip().upper()
        por_base.setdefault(base, []).append(str(c["ticker"]))

    # ticker de 1816 → (nuestro ticker, qué pata es)
    pedidos: dict[str, tuple[str, str]] = {}
    print(f"  {'TICKER':<9}{'PATAS':<24}{'VTO':<12}{'EMISOR':<13}{'QUÉ SE LE PIDE A 1816'}")
    print("  " + "-" * 96)
    for t in tamar:
        tk = str(t["ticker"]).upper()
        patas = t["ajuste"] + (f" + {t['ajuste_alt']}" if t["ajuste_alt"] else "")
        variantes = sorted(por_base.get(tk, []))
        if variantes:
            for v in variantes:
                pedidos[v] = (tk, v.split("@")[1].strip() if "@" in v else "—")
            que = " · ".join(variantes)
        else:
            pedidos[tk] = (tk, "pelado")
            que = tk
        print(f"  {tk[:8]:<9}{patas[:23]:<24}"
              f"{str(t['fecha_vencimiento'] or '—')[:10]:<12}"
              f"{str(t['emisor_tipo'] or '—')[:12]:<13}{que[:44]}")

    # ¿1816 parte en dos algún bono que nosotros NO tenemos marcado como dual?
    nuestros = {str(t["ticker"]).upper() for t in tamar}
    huerfanos = sorted(b for b in por_base if b not in nuestros)
    if huerfanos:
        print(f"\n  ⚠️ 1816 parte en patas {len(huerfanos)} instrumentos que NO están en")
        print("     nuestro universo TAMAR — puede ser que les falte `ajuste_alt`:")
        for h in huerfanos:
            print(f"       {h:<8} → {' · '.join(sorted(por_base[h]))}")

    print(f"\n  → {len(pedidos)} tickers a consultar × {len(_CAMPOS)} campos "
          f"= ~{len(pedidos) * len(_CAMPOS)} créditos (de 100.000 diarios)")

    if not pedir:
        print(f"\n{_SEP}\n  BLOQUE 2 — cuesta créditos, no se corrió\n{_SEP}")
        print("  Para ejecutarlo:  python -m scripts.diag_tamar_1816 --pedir")
        print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")
        return

    from core import mercado_1816
    if not mercado_1816.disponible():
        print("\n  ⚠️ 1816 no está configurado en este entorno.")
        return

    try:
        bal = mercado_1816.balance()
        print(f"\n  CRÉDITOS antes: {json.dumps(bal, default=str)}")
    except Exception as e:
        print(f"\n  ⚠️ saldo no disponible: {e}")

    # ── 2. LOS VALORES (cuesta créditos) ────────────────────────────────────
    print(f"\n{_SEP}\n  2 · LOS VALORES DE 1816\n{_SEP}")

    # ⚠️ SIEMPRE con fecha explícita. Sin ella la API usa HOY y un domingo
    # devuelve los 6 campos en null — que es exactamente lo que nos hizo dudar
    # de si `spread` existía. Si la fecha elegida vuelve vacía (feriado), se
    # retrocede hábil por hábil hasta encontrar una con datos.
    d = dt.date.fromisoformat(fecha) if fecha else _habil_anterior(dt.date.today())
    tickers = sorted(pedidos)
    inst: dict = {}
    resp: dict = {}
    for _ in range(_MAX_RETROCESO):
        print(f"\n  Pidiendo fechaOperacion={d} …")
        try:
            resp = mercado_1816.indicadores(tickers, _CAMPOS,
                                            fecha_operacion=d.isoformat())
        except Exception as e:
            print(f"    ❌ {type(e).__name__}: {str(e)[:160]}")
            return
        inst = resp.get("instrumentos") or {}
        con_dato = sum(1 for v in inst.values()
                       if any(x is not None for x in (v or {}).values()))
        print(f"    {len(inst)} instrumentos, {con_dato} con algún valor")
        if con_dato:
            break
        print("    (todo null → feriado o sin rueda; retrocedo un hábil)")
        d = _habil_anterior(d)
    else:
        print(f"\n  ❌ {_MAX_RETROCESO} días hábiles seguidos sin datos. Algo más pasa.")
        return

    print(f"\n  fechaOperacion que ECHA 1816: {resp.get('fechaOperacion')} · "
          f"fuente={resp.get('fuente')} · plazo={resp.get('plazo')} · "
          f"moneda={resp.get('moneda')}\n")
    print(f"  {'TICKER 1816':<22}{'PATA':<12}{'TEA':>9}{'TNA':>9}{'SPREAD':>9}"
          f"{'PRECIO':>9}{'DUR':>8}{'PARIDAD':>9}")
    print("  " + "-" * 96)
    # Se recorre `pedidos` y no `inst` para que un ticker que 1816 NO devolvió
    # aparezca igual como fila vacía — un faltante silencioso es justo lo que
    # después no se nota en el job.
    for tk1816 in tickers:
        nuestro, pata = pedidos[tk1816]
        v = inst.get(tk1816) or {}
        marca = "" if v else "   ← 1816 no lo devolvió"
        print(f"  {tk1816[:21]:<22}{pata[:11]:<12}"
              f"{_fmt(v.get('tea')):>9}{_fmt(v.get('tna')):>9}"
              f"{_fmt(v.get('spread')):>9}{_fmt(v.get('precioClean')):>9}"
              f"{_fmt(v.get('duration')):>8}{_fmt(v.get('paridad')):>9}{marca}")

    # ── 3. ¿LAS DOS PATAS DE UN DUAL RINDEN DISTINTO? ───────────────────────
    print(f"\n{_SEP}\n  3 · DUALES — ¿cada pata rinde distinto?\n{_SEP}")
    por_bono: dict[str, list[tuple[str, str, dict]]] = {}
    for tk1816, (nuestro, pata) in pedidos.items():
        por_bono.setdefault(nuestro, []).append((tk1816, pata, inst.get(tk1816) or {}))
    hubo = False
    for nuestro, filas in sorted(por_bono.items()):
        if len(filas) < 2:
            continue
        hubo = True
        teas = [(p, (v.get("tea"))) for _, p, v in filas]
        print(f"\n  {nuestro}:")
        for _, pata, v in sorted(filas):
            print(f"     {pata:<12} TEA {_fmt(v.get('tea')):>8}   "
                  f"spread {_fmt(v.get('spread')):>8}   px {_fmt(v.get('precioClean')):>8}")
        vals = [t for _, t in teas if t is not None]
        if len(vals) >= 2:
            dif = (max(vals) - min(vals)) * 100
            print(f"     → diferencia entre patas: {dif:.0f} bps")
    if not hubo:
        print("\n  (ningún bono con más de una pata en la respuesta)")

    print(f"\n{_SEP}\n  CÓMO LEERLO\n{_SEP}")
    print("  · Si `spread` de TXMD9 @TAMAR ≈ 9,73 → ESE es el «Margen» del header")
    print("    de 1816 y el job queda cerrado: pedir esos 6 campos y persistir.")
    print("  · Si a los CORPORATIVOS les vuelve `spread` vacío, el margen lo")
    print("    tendremos solo para los soberanos — hay que decidir qué mostrar en")
    print("    la columna del resto (vacío es honesto; un 0 sería mentira).")
    print("  · Si las dos patas de un dual dan TEAs distintas, el problema de los")
    print("    duales se resuelve con ESTA MISMA ingesta: una fila por pata.")
    print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")


if __name__ == "__main__":
    main()
