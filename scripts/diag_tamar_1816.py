"""scripts/diag_tamar_1816.py — ¿cómo le pido a 1816 la TEA y el MARGEN de un TAMAR?

**Decisión tomada (2026-08-16):** los TAMAR no los vamos a valuar nosotros — se
traen de 1816. El motivo no es pereza: la planilla de la mesa y el header de 1816
dan **el mismo número** (TXMD9 → TEA 38,55% vs 38,62%; spread 9,71% vs 9,73%; el
MISMO precio 84,10). O sea que la mesa ya valida contra 1816. Reimplementar la
metodología nos pondría a competir contra el número que ellos ya miran, y si el
nuestro difiere en 3 puntos básicos nadie usaría el nuestro aunque tuviera razón.

Hoy los TAMAR **no tienen ni cálculo**: caen en el `else` del motor (solo
duration). No están mal valuados — están sin valuar.

**Lo que este discovery tiene que averiguar**, que es lo único que falta para
escribir el job:

  1. ¿Las variantes `@TAMAR` / `@CER` están en NUESTRO catálogo de 1816, y con qué
     grafía exacta? (`TXMD9 @TAMAR`, `TXMD9@TAMAR`, otra). — **GRATIS**, ya está
     en `research.mkt_1816_instrumentos`.
  2. Si no están: cómo las nombra 1816. — 1 crédito (`instrumentos`).
  3. **Qué CAMPO devuelve el margen.** El header de 1816 muestra «9,73% Margen»,
     pero el nombre del campo en la API no lo sabemos. Se prueba una lista de
     candidatos sobre UN ticker. — pocos créditos (`indicadores` = tickers × campos).

Sabemos que `tea`, `paridad`, `precioClean` y `duration` son válidos (los usa
`jobs/mercado_1816_series`), y que existen `tna` y `spread`. El candidato fuerte
es `spread` o `margen`, pero **no está verificado** — de eso se trata esto.

READ-ONLY: no escribe en la base. Los pasos 2 y 3 consumen créditos y NO corren
sin `--pedir`.

Uso:
    python -m scripts.diag_tamar_1816              # gratis: paso 1
    python -m scripts.diag_tamar_1816 --pedir      # + pasos 2 y 3
"""
from __future__ import annotations

import json
import sys

from core.postgres import get_pool

_SEP = "=" * 100

# Nombres CANDIDATOS para el margen. Los cuatro primeros ya se sabe que existen
# (los usa jobs/mercado_1816_series); el resto son la apuesta. Se prueban de a uno
# para que un nombre inválido no tumbe a los demás — la API rechaza la llamada
# entera si un campo no existe, así que pedirlos todos juntos no distingue cuál falló.
_CANDIDATOS = ["tea", "tna", "precioClean", "duration", "paridad",
               "spread", "margen", "margin", "spreadTamar", "margenTamar"]


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def main() -> None:
    pedir = "--pedir" in sys.argv
    print(_SEP)
    print("TAMAR — cómo pedirle a 1816 la TEA y el MARGEN")
    print(_SEP)

    # ── 1. NUESTRO universo TAMAR + las variantes en el catálogo (GRATIS) ────
    tamar = _q("""
        SELECT ticker, ajuste, ajuste_alt, fecha_vencimiento, emisor_tipo
        FROM mercado.curvas
        WHERE ajuste = 'tamar' OR ajuste_alt = 'tamar'
        ORDER BY fecha_vencimiento, ticker
    """)
    print(f"\n  1 · NUESTRO UNIVERSO — {len(tamar)} bonos con pata TAMAR\n")
    print(f"  {'TICKER':<9}{'PATAS':<24}{'VTO':<12}{'EMISOR':<12}")
    print("  " + "-" * 60)
    for t in tamar:
        patas = t["ajuste"] + (f" + {t['ajuste_alt']}" if t["ajuste_alt"] else "")
        dual = "  (dual)" if t["ajuste_alt"] else ""
        print(f"  {str(t['ticker'])[:8]:<9}{patas[:23]:<24}"
              f"{str(t['fecha_vencimiento'] or '—')[:10]:<12}"
              f"{str(t['emisor_tipo'] or '—')[:11]:<12}{dual}")

    # ¿Las variantes ya están en el catálogo que bajamos de 1816?
    print(f"\n{_SEP}\n  Variantes @TAMAR / @CER en NUESTRO catálogo de 1816 (gratis)\n{_SEP}")
    variantes = _q("""
        SELECT ticker, denominacion, curva, fecha_vencimiento
        FROM research.mkt_1816_instrumentos
        WHERE ticker ILIKE '%%@%%' OR denominacion ILIKE '%%@%%'
        ORDER BY ticker
    """)
    if variantes:
        print(f"\n  ✅ {len(variantes)} instrumentos con '@'. Así los nombra 1816:\n")
        for v in variantes[:40]:
            print(f"     ticker={v['ticker']!r:<24} denominacion={v['denominacion']!r}")
        if len(variantes) > 40:
            print(f"     … y {len(variantes) - 40} más")
        print("\n  👉 ESA es la grafía exacta que hay que usar para pedirlos.")
    else:
        print("\n  ⚠️ NINGUNA variante '@' en el catálogo.")
        print("     Puede ser que `mercado_1816_discovery --catalogo` recorra solo las")
        print("     28 curvas y las variantes no cuelguen de ninguna. El paso 2 lo aclara.")

    if not pedir:
        print(f"\n{_SEP}\n  PASOS 2 y 3 — cuestan créditos, no se corrieron\n{_SEP}")
        print("  Para ejecutarlos:  python -m scripts.diag_tamar_1816 --pedir")
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

    # Un ticker de prueba: el que ya vimos en pantalla, así el número es comparable.
    prueba = "TXMD9"

    # ── 2. Cómo nombra 1816 las variantes (1 crédito) ───────────────────────
    print(f"\n{_SEP}\n  2 · ¿Cómo nombra 1816 las variantes de {prueba}? (1 crédito)\n{_SEP}")
    try:
        encontrados = mercado_1816.instrumentos(texto=prueba)
        print(f"\n  {len(encontrados)} resultados para «{prueba}»:\n")
        for i in encontrados:
            print(f"     {json.dumps(i, ensure_ascii=False, default=str)[:180]}")
    except Exception as e:
        print(f"\n  ❌ {type(e).__name__}: {str(e)[:160]}")
        encontrados = []

    # ── 3. QUÉ CAMPO ES EL MARGEN (el punto de todo esto) ───────────────────
    print(f"\n{_SEP}\n  3 · ¿Qué CAMPO devuelve el MARGEN? (1 crédito por campo probado)\n{_SEP}")
    print("  El header de 1816 muestra «9,73% Margen» para TXMD9 @TAMAR. Buscamos")
    print("  el nombre de ese campo en la API. Se prueban de a UNO: la API rechaza")
    print("  la llamada entera si un campo no existe, así que en lote no se sabría")
    print("  cuál falló.\n")

    # El ticker a consultar: si el paso 2 encontró la variante @TAMAR, se usa ESA
    # (es la que trae el margen). Si no, el pelado — y el resultado lo dirá.
    objetivo = prueba
    for i in encontrados:
        tk = str(i.get("ticker") or "")
        if "@TAMAR" in tk.upper():
            objetivo = tk
            break
    print(f"  Ticker consultado: {objetivo!r}\n")
    print(f"  {'CAMPO':<16}{'RESULTADO'}")
    print("  " + "-" * 80)
    validos: dict[str, object] = {}
    for campo in _CANDIDATOS:
        try:
            r = mercado_1816.indicadores([objetivo], [campo])
            inst = (r.get("instrumentos") or {})
            # La API puede devolver la clave con otra grafía → se toma el primero.
            valores = next(iter(inst.values()), {}) if inst else {}
            v = valores.get(campo, valores)
            validos[campo] = v
            print(f"  {campo:<16}✅ {json.dumps(v, ensure_ascii=False, default=str)[:60]}")
        except Exception as e:
            print(f"  {campo:<16}❌ {type(e).__name__}: {str(e)[:56]}")

    print(f"\n{_SEP}\n  CÓMO LEERLO\n{_SEP}")
    print("  Buscá el campo cuyo valor esté cerca de 9,73 (o 0,0973). ESE es el margen.")
    print("  Con ese nombre + la grafía del ticker del paso 2, el job es directo:")
    print("  pedir [tea, tna, <margen>, precioClean] para los tickers con pata TAMAR,")
    print("  cada 30' de 10 a 17 en días hábiles, y persistir con su fechaOperacion.")
    print("\n  Si NINGÚN candidato devuelve ~9,73: el margen no sale de `indicadores`")
    print("  y hay que buscarlo en otro endpoint — pero al menos ya sabremos que no")
    print("  está ahí, que es la mitad de la respuesta.")
    print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")


if __name__ == "__main__":
    main()
