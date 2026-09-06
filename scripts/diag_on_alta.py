"""`scripts/diag_on_alta.py` — **¿SE PUEDEN DAR DE ALTA SOLAS LAS ONs?**

Read-only. **No escribe una sola fila.** Doc: `docs/AGENT.md` §0.dp (la habilidad
`on_faltante`), §0.dr (esta medición) y §0.ds (qué dijo).

## Qué contesta

**Cuáles de las ONs que faltan se pueden dar de alta, y cuáles no.**

El botón ya existe (§0.du): la rama `on` está en `alta.RAMAS_AUTOMATICAS`, así
que el paso `rama` no bloquea a todas por adelantado y **cada bono lo juzga su
propio cotejo contra 1816**. Este diag corre ese mismo cotejo en lote, para ver
de antemano cuántas van a pasar sin ir apretando una por una.

Lo que decide es **la duration**, no la TEA ni la paridad, y hay una razón
medida: 1816 anualiza 180-360 y nosotros con días reales, así que la TEA nunca
cierra —ni en los bonos que están perfectos—; y la paridad de ellos incluye el
interés corrido y la nuestra no. La duration sale SOLO del cronograma y las
fechas: si coincide, el cuadro que escribiríamos es el de ellos.

## Qué hace este diag

Corre `alta.simular()` —la MISMA función que usa el botón— sobre las ONs que el
agente tiene abiertas, y reporta por cada una:

    escala del cuadro · Σ amortizaciones · cuántos cupones
    la PARIDAD nuestra contra la de 1816 (el control cruzado)
    el veredicto del pre-flight: ¿puede aplicar? ¿puede aplicar SOLO?

Si el control cruzado cierra en todas, la rama `on` se prende y el aviso se
convierte en un botón. Si no cierra en alguna, este diag dice en cuál y por qué.

⚠️ **NO reimplementa nada**: el universo sale de `agente.hallazgos` (lo que el
detector ya encontró, con su `curva_1816` en la evidencia) y la conversión sale
de `agente.alta`. Dos definiciones distintas de lo mismo darían números que no
fallan y no coinciden (REGLA #9).

## Costo

⚠️ **ESTA ES LA PARTE CARA Y SALE A LA RED.** `simular` le pide a 1816 el cuadro
de flujos, y eso **cuesta un crédito por cupón**. Por eso hay tope y es chico por
default: se mira una muestra, se lee el resultado, y recién ahí se sube.

    python -m scripts.diag_on_alta                 # 6 ONs (las de cartera primero)
    python -m scripts.diag_on_alta --tope 20       # una muestra más grande
    python -m scripts.diag_on_alta --ticker CP37O  # una sola, con el detalle
    python -m scripts.diag_on_alta --solo-cartera  # solo las que HOY no valúan
"""
from __future__ import annotations

import argparse
import sys

from core.postgres import get_pool

HABILIDAD = "on_faltante"
REGLA = "no_esta_en_curvas"


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def universo(*, solo_cartera: bool) -> list[dict]:
    """Las ONs que el AGENTE tiene abiertas, con la curva de 1816 que él mismo
    guardó en la evidencia. Las de CARTERA primero: esas hoy no valúan."""
    from agente import tipos
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT sujeto, evidencia FROM agente.hallazgos "
            " WHERE habilidad = %s AND regla = %s AND estado = ANY(%s) "
            " ORDER BY (evidencia->>'en_cartera')::bool DESC NULLS LAST, sujeto",
            (HABILIDAD, REGLA, list(tipos.ABIERTOS)))
        filas = cur.fetchall()
    out = []
    for sujeto, ev in filas:
        ev = ev or {}
        if solo_cartera and not ev.get("en_cartera"):
            continue
        out.append({"ticker": sujeto, "curva_1816": ev.get("curva_1816") or "",
                    "emisor": ev.get("emisor") or "?",
                    "en_cartera": bool(ev.get("en_cartera")),
                    "vencimiento": ev.get("vencimiento_1816") or ""})
    return out


def _pct(v) -> str:
    return f"{float(v):.2f}%" if isinstance(v, int | float) else "—"


def _paso(sim: dict, clave: str) -> dict:
    for c in sim.get("chequeos") or []:
        if c.get("clave") == clave:
            return c
    return {}


def _una(on: dict, *, detalle: bool) -> dict:
    """Simula UNA ON y devuelve lo que hay que mirar. Nunca levanta."""
    from agente import alta
    try:
        sim = alta.simular(on["ticker"], curva_1816=on["curva_1816"])
    except Exception as e:                                   # pragma: no cover
        return {**on, "error": f"{type(e).__name__}: {e}"[:160]}
    if not sim.get("ok"):
        return {**on, "error": (sim.get("error") or "sin motivo")[:160]}

    conv = sim.get("cuadro") or {}
    ver = sim.get("veredicto") or {}
    cot = _paso(sim, "cotejo_1816")
    # ⚠️ DOS NIVELES, y confundirlos vació la columna que decide. La PARIDAD
    # comparable es la de `a_nuestro_precio` (1816 recalculada a NUESTRO precio);
    # la DURATION vive en el nivel de arriba, porque no depende del precio — que
    # es justamente lo que la hace el testigo bueno. `_cotejo_tea` lee cada una
    # de su lugar; la primera versión de este diag leyó las dos del sub-dict y
    # mostró «—» donde estaba la respuesta.
    r16 = sim.get("referencia_1816") or {}
    ref = r16.get("a_nuestro_precio") or {}
    r = {**on, "error": "",
         "rama": sim.get("rama"), "escala": conv.get("escala"),
         "suma_amort": conv.get("suma_amort"), "n": conv.get("n"),
         "tea": sim.get("tea"), "paridad": sim.get("paridad"),
         # ⚠️ ESCALA: nuestro motor devuelve la paridad en PORCENTAJE (98.11) y
         # 1816 la publica como FRACCIÓN (0.9811). La primera versión de este
         # diag las imprimía crudas y mostraba «0.98%» al lado de «102.77%» —
         # dos números que parecían contradecirse y no lo hacían. Es el mismo
         # bug que `_cotejo_tea` documenta y evita adentro.
         "paridad_1816": (ref.get("paridad") * 100
                          if isinstance(ref.get("paridad"), int | float) else None),
         # EL TESTIGO QUE DECIDE. No depende del precio ni del tipo de cambio ni
         # del interés corrido: si la duration coincide, el cronograma ES el de
         # ellos y la diferencia de paridad es definición, no error.
         "duration": sim.get("duration"),
         "duration_1816": r16.get("duration"),
         "cotejo": cot.get("estado") or "—", "cotejo_txt": cot.get("detalle") or "",
         "puede_aplicar": ver.get("puede_aplicar"),
         "puede_auto": ver.get("puede_auto"),
         "bloqueos": [c["titulo"] for c in sim.get("chequeos") or []
                      if c["estado"] == alta.BLOQUEA],
         # Por CLAVE, no por título: el título es texto para leer y cambia; la
         # clave es la identidad del paso (REGLA #9).
         "claves_bloqueo": [c["clave"] for c in sim.get("chequeos") or []
                            if c["estado"] == alta.BLOQUEA],
         "frenan_auto": [c["titulo"] for c in sim.get("chequeos") or []
                         if c["estado"] in (alta.REVISAR, alta.NO_SE)],
         "motivo_no_aplicable": sim.get("motivo_no_aplicable") or ""}
    if detalle:
        r["flujos"] = conv.get("flujos", [])[:4]
    return r


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tope", type=int, default=6,
                   help="cuántas ONs simular (cuesta créditos de 1816)")
    p.add_argument("--ticker", default="", help="simular UNA sola, con detalle")
    p.add_argument("--solo-cartera", action="store_true",
                   help="solo las que la mesa TIENE y hoy no valúan")
    a = p.parse_args()

    from agente import alta

    todas = universo(solo_cartera=a.solo_cartera)
    if a.ticker:
        tk = a.ticker.strip().upper()
        todas = [o for o in todas if o["ticker"] == tk]
        if not todas:
            print(f"«{tk}» no está entre las ONs abiertas de `{HABILIDAD}`.")
            return 1

    _titulo("0. EL UNIVERSO Y LA RAMA")
    print(f"  `{HABILIDAD} · {REGLA}` abiertas: {len(todas)}"
          + (f" (filtrado a {a.ticker})" if a.ticker else "")
          + (" · SOLO las de cartera" if a.solo_cartera else "") + "\n")
    print(f"  `alta.RAMAS_AUTOMATICAS` = {alta.RAMAS_AUTOMATICAS}")
    print("  La rama de un corporativo es «on» y YA ESTÁ en esa tupla (§0.du): el\n"
          "  paso `rama` no bloquea más en bloque, así que **cada bono lo juzga su\n"
          "  propio cotejo contra 1816**. Este diag muestra cuál pasa y cuál no.\n")
    if not todas:
        print("  No hay ONs abiertas: nada que medir.")
        return 0

    muestra = todas if a.ticker else todas[:max(0, a.tope)]
    _titulo(f"1. LA CONVERSIÓN, MEDIDA — muestra de {len(muestra)} de {len(todas)}")
    print("  ⚠️ Sale a la red: 1816 cobra un crédito POR CUPÓN.\n"
          "  La columna que decide es COTEJO: compara nuestra PARIDAD contra la\n"
          "  de 1816 AL MISMO PRECIO. Un cuadro mal escalado no da error — da un\n"
          "  número plausible y equivocado, y esto es lo que lo caza.\n")

    res = [_una(o, detalle=bool(a.ticker)) for o in muestra]

    print(f"  {'TICKER':<8} {'ESCALA':<9} {'ΣAMORT':>10} {'CUP':>4} "
          f"{'PARIDAD':>9} {'1816':>9} {'DURAT.':>8} {'1816':>8} {'Δdur':>7} "
          f"{'COTEJO':<10}")
    print("  " + "─" * 92)
    for r in res:
        if r["error"]:
            print(f"  {r['ticker']:<8} {'sí' if r['en_cartera'] else '':<5} "
                  f"⚠ {r['error'][:60]}")
            continue
        sa, d, d16 = r["suma_amort"], r["duration"], r["duration_1816"]
        ddif = (abs(d - d16) / d16 * 100
                if isinstance(d, int | float) and isinstance(d16, int | float) and d16
                else None)
        print(f"  {r['ticker']:<8} {r['escala']!s:<9} "
              f"{(f'{sa:,.4f}' if sa is not None else '—'):>10} "
              f"{r['n']!s:>4} {_pct(r['paridad']):>9} {_pct(r['paridad_1816']):>9} "
              f"{(f'{d:.4f}' if isinstance(d, int | float) else '—'):>8} "
              f"{(f'{d16:.4f}' if isinstance(d16, int | float) else '—'):>8} "
              f"{(f'{ddif:.2f}%' if ddif is not None else '—'):>7} {r['cotejo']:<10}")

    _titulo("2. EL VEREDICTO DEL PRE-FLIGHT, ON POR ON")
    print("  `puede_aplicar` = lo puede apretar una persona (solo lo frena algo\n"
          "  PROBADO mal). `puede_auto` = se podría aplicar sin que nadie mire\n"
          "  (además lo frenan «se midió y no cierra» y «no se pudo verificar»).\n"
          "  Hoy los dos dan False por la MISMA razón: la rama.\n")
    for r in res:
        if r["error"]:
            continue
        print(f"  · {r['ticker']} ({r['emisor']}) — aplicar={r['puede_aplicar']} "
              f"auto={r['puede_auto']}")
        if r["bloqueos"]:
            print(f"      ✖ bloquea: {'; '.join(r['bloqueos'])}")
        if r["frenan_auto"]:
            print(f"      ▲/? frena el automático: {'; '.join(r['frenan_auto'])}")
        if r["cotejo_txt"]:
            # SIN truncar. La primera versión cortaba en 150 y se comía justo la
            # duration, que es el número que decide si el cronograma es el mismo.
            print(f"      cotejo: {r['cotejo_txt']}")
        if r.get("flujos"):
            print(f"      primeros flujos: {r['flujos']}")

    _titulo("3. LA CONCLUSIÓN — qué habilita y qué no")
    ok = [r for r in res if not r["error"]]
    escalas = {r["escala"] for r in ok}
    cotejos = {r["cotejo"] for r in ok}
    solo_rama = [r for r in ok if r["claves_bloqueo"] == ["rama"]]
    print(f"  escalas encontradas: {escalas or '—'}")
    print(f"  estados del cotejo:  {cotejos or '—'}")
    print(f"  simuladas ok: {len(ok)} de {len(res)}\n")
    print("  ⚠️ **LA COLUMNA QUE DECIDE ES Δdur, NO LA PARIDAD.**\n"
          "  La duration no depende del precio, ni del tipo de cambio, ni del\n"
          "  interés corrido: sale SOLO del cronograma y las fechas.\n\n"
          "  · Δdur ≈ 0 → el cronograma que escribiríamos ES el de 1816. Una\n"
          "    paridad más alta que la de ellos es entonces DEFINICIÓN (nosotros\n"
          "    dividimos por el residual, ellos por el valor técnico), no un error.\n"
          "  · Δdur > 5% → bajamos otro cuadro. Eso sí es un problema, y es el\n"
          "    único caso en que el cotejo BLOQUEA.\n"
          "  · `nominales` en ESCALA → habría que normalizar por la Σ, como ya\n"
          "    hace `dolar_linked`. Si todas dan `vn100`, ese miedo no aplica.")
    if solo_rama:
        print(f"\n  ⚠️ {len(solo_rama)} de {len(ok)} tienen como ÚNICO bloqueo la rama:\n"
              "  o sea, el resto de la cadena ya está en verde para ellas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
