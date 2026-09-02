"""`scripts/diag_tasa_fija.py` — AUDITORÍA DE LA PILL **TASA FIJA**: ¿el número
que muestra la pantalla es el que sale de recalcularlo, y coincide con 1816?

Read-only. No escribe una sola fila.

**Por qué existe.** Una TNA equivocada no falla: la celda tiene un número, la
tabla suma, el gráfico dibuja. Solo se nota comparando contra otro que calculó lo
mismo. Este script hace las DOS comparaciones que hay que hacer y, sobre todo,
las **separa** — porque una diferencia puede venir de tres lugares distintos y en
pantalla los tres se ven igual:

  **(1) El número guardado no corresponde a su propio precio.** El motor
  recalcula SOLO cuando cambia el `last_price` (cache `ultimo_calculado` en
  `engines/curvas.py`). Se recalcula acá con los MISMOS insumos y la misma
  función que usa el motor: si `guardado ≠ recalculado`, lo que está en la base
  quedó de un cálculo anterior.

  **(2) El precio de arranque es otro.** Nosotros usamos el `last_price` de
  Primary (live); 1816 publica su `precioClean` de BYMA. Con precios distintos
  las tasas TIENEN que dar distinto y no hay nada roto. Por eso el script
  recalcula **nuestra fórmula con el precio de ELLOS**: si ahí da su TEA, el
  cálculo está bien y la diferencia era el precio. Es la prueba decisiva y es la
  que evita salir a tocar el motor por un problema que no tiene.

  **(3) La convención de TNA no es la misma.** La casa deriva `TNA = TEM × 12`
  con `TEM = (1+TEA)^(1/12) − 1` (`quant/tasas.py`). 1816 publica su propia
  `tna`, y si no cumple esa identidad entonces las dos columnas NO son
  comparables aunque la TEA coincida. El script lo verifica contra la TEA del
  propio 1816, así que la respuesta no depende de que nuestro precio coincida.

El universo NO se arma acá: sale de `curvas_vista.get_curvas_vista()`, o sea de
lo que la pantalla muestra de verdad — pill `tasa_fija`, con los CER ya fijados
adentro, que es justamente donde se mezclan dos familias.

Créditos de 1816: **tickers × campos**. `--dry` imprime el costo sin pegar y
`--sin-1816` corre solo la parte local, que es gratis.

Uso:
    python -m scripts.diag_tasa_fija                # todo
    python -m scripts.diag_tasa_fija --dry          # universo + costo, sin pegar
    python -m scripts.diag_tasa_fija --sin-1816     # solo recálculo local
    python -m scripts.diag_tasa_fija --ticker S31G6 # un bono, paso por paso
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

# Diferencias a partir de las cuales se reporta. En puntos porcentuales de TNA.
# No son umbrales de "está bien / está mal": son el corte de lo que se IMPRIME,
# para que la tabla entre en una pantalla. El detalle por bono sale con --ticker.
DIF_REPORTA = 0.10       # 10 bps de TNA
DIF_PRECIO = 0.005       # 0,5% de precio


def _tna(tea):
    """TNA nominal anual, capitalización mensual — la convención de la casa."""
    if tea is None:
        return None
    tea = float(tea)
    if tea <= -1:
        return None
    return ((1 + tea) ** (1 / 12) - 1) * 12


def _pct(v, d=2):
    return "  --  " if v is None else f"{v * 100:.{d}f}%"


def _dif(a, b):
    """Diferencia en PUNTOS PORCENTUALES de TNA (no en %, no en bps)."""
    return None if a is None or b is None else (a - b) * 100


def _q(sql: str, params: tuple = ()) -> list[dict]:
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# EL UNIVERSO — lo que la pantalla muestra en TASA FIJA
# ─────────────────────────────────────────────────────────────────────────────
def universo() -> list[dict]:
    """Los bonos de la pill `tasa_fija`, tal como los sirve la vista.

    Se le pregunta a `curvas_vista` y no se rearma la condición acá: si este
    script definiera "qué es tasa fija" por su cuenta, auditaría un universo que
    no es el de la pantalla y podría dar todo verde sobre otros bonos.
    """
    from api.services.curvas_vista import get_curvas_vista
    v = get_curvas_vista()
    return [b for b in (v.get("bonos") or []) if b.get("pill") == "tasa_fija"]


def snapshot_de(simbolos: list[str]) -> dict[str, dict]:
    """`símbolo → fila cruda de market_snapshot`, con el `updated_at`.

    La vista no manda `updated_at` y el recálculo lo necesita: el motor arma su
    fecha de cálculo con ESE timestamp (`engines/curvas.py` → `fake_doc`).
    """
    if not simbolos:
        return {}
    return {r["ticker"]: r for r in _q(
        "SELECT ticker, last_price, tea, tem, duration, updated_at "
        "  FROM mercado.market_snapshot WHERE ticker = ANY(%s)", (simbolos,))}


# ─────────────────────────────────────────────────────────────────────────────
# (1) RECALCULAR con la MISMA función del motor
# ─────────────────────────────────────────────────────────────────────────────
def motor_insumos():
    """Los cuatro insumos que `calcular_campos` necesita, cargados igual que en
    `engines/curvas.run()`. Se importan del motor para que no exista una segunda
    copia de la carga que pueda divergir."""
    from engines import curvas as mc
    return {
        "curvas": mc.cargar_indexado_por_ticker(),
        "cer": mc.cargar_cer(),
        "habiles": mc.cargar_dias_habiles(),
        "mep": mc.cargar_mep_actual(),
        "a3500": mc.cargar_a3500_actual(),
    }


def recalcular(ins: dict, simbolo: str, precio, cuando: datetime) -> dict | None:
    """`calcular_campos` del motor, con el precio y la fecha que se le pasen.

    Devuelve `None` cuando el motor tampoco puede — que es un resultado, no un
    error: significa que la celda vacía de la pantalla está bien.
    """
    from engines.curvas import calcular_campos
    inst = ins["curvas"].get(simbolo)
    if not inst or not precio:
        return None
    doc = {"ticker": simbolo, "price": float(precio), "timestamp": cuando}
    try:
        return calcular_campos(doc, inst, ins["cer"], ins["habiles"],
                               ins["mep"], ins["a3500"]) or None
    except Exception as e:                                    # pragma: no cover
        return {"_error": str(e)[:120]}


# ─────────────────────────────────────────────────────────────────────────────
# (3) 1816 — la grafía la manda SU catálogo, no nosotros
# ─────────────────────────────────────────────────────────────────────────────
def grafias(tickers: list[str]) -> dict[str, str]:
    """`grafía de 1816 → nuestro ticker`, para la pata de TASA FIJA.

    Mismo criterio que `jobs/tamar_1816.universo()`, y se le importa la tabla de
    sufijos en vez de repetirla: un sufijo nuevo tiene que cambiar en UN lugar.
    Un bono de tasa fija pura no tiene variantes con `@` y se pide pelado; los
    duales sí (`TTD26 @TASA FIJA` / `@BONCAP`), y ahí se piden TODAS las que
    mapean a `fija` — la API rechaza la llamada entera si un ticker no existe,
    pero no si no tiene datos, así que pedir de más solo cuesta créditos.
    """
    from jobs.tamar_1816 import _SUFIJO_A_AJUSTE, _sufijo
    variantes = _q("SELECT ticker, denominacion FROM research.mkt_1816_instrumentos "
                   " WHERE ticker ILIKE '%%@%%'")
    por_base: dict[str, list[dict]] = {}
    for v in variantes:
        por_base.setdefault(str(v["ticker"]).split("@", 1)[0].strip().upper(),
                            []).append(v)
    out: dict[str, str] = {}
    for tk in tickers:
        vs = por_base.get(tk.upper())
        if not vs:
            out[tk] = tk                      # tasa fija pura: el ticker pelado
            continue
        for v in vs:
            g = str(v["ticker"])
            if _SUFIJO_A_AJUSTE.get(_sufijo(g)) == "fija":
                out[g] = tk
            suf_den = _sufijo(str(v.get("denominacion") or ""))
            if _SUFIJO_A_AJUSTE.get(suf_den) == "fija":
                out[f"{tk} @{suf_den}"] = tk
    return out


CAMPOS_1816 = ["tea", "tna", "duration", "precioClean"]


def pedir_1816(tickers: list[str]) -> tuple[dict[str, dict], str | None]:
    """`nuestro ticker → {tea, tna, duration, precioClean}` + la rueda que salió.

    Una sola llamada. `indicadores_vigentes` retrocede día hábil por hábil hasta
    encontrar una rueda con datos: sin eso, un lunes temprano devuelve todo en
    `null` y parecería que 1816 no tiene el bono.
    """
    from core import mercado_1816
    if not mercado_1816.disponible():
        print("  ⚠️  MERCADO_1816_API_KEY no está configurada — se saltea 1816.")
        return {}, None
    g = grafias(tickers)
    resp = mercado_1816.indicadores_vigentes(sorted(g), CAMPOS_1816) or {}
    inst = resp.get("instrumentos") or {}
    out: dict[str, dict] = {}
    for grafia, nuestro in g.items():
        v = inst.get(grafia) or {}
        if v.get("tea") is None and v.get("precioClean") is None:
            continue
        # Gana la grafía que trajo TEA (el alias hace que un dual se pida dos veces).
        if nuestro not in out or (out[nuestro].get("tea") is None
                                  and v.get("tea") is not None):
            out[nuestro] = {"grafia": grafia, **v}
    return out, resp.get("fechaOperacion")


# ─────────────────────────────────────────────────────────────────────────────
def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def auditar(bonos: list[dict], ins: dict, snaps: dict,
            d1816: dict[str, dict]) -> list[dict]:
    ahora = datetime.now(UTC)
    filas = []
    for b in bonos:
        tk, sim = b["ticker_corto"], b.get("instrumento")
        m = b.get("metrics") or {}
        s = snaps.get(sim) or {}
        precio = _f(s.get("last_price"))
        upd = s.get("updated_at") or ahora

        # Lo que el motor sacaría HOY con el mismo precio y la misma fecha base
        # que usó él (`updated_at`), y lo que sacaría fechado AHORA.
        r_motor = recalcular(ins, sim, precio, upd) if sim else None
        r_hoy = recalcular(ins, sim, precio, ahora) if sim else None

        o = d1816.get(tk) or {}
        p1816 = _f(o.get("precioClean"))
        # LA PRUEBA DECISIVA: nuestra fórmula, su precio.
        r_su_precio = recalcular(ins, sim, p1816, ahora) if (sim and p1816) else None

        filas.append({
            "tk": tk, "simbolo": sim, "cer_fijado": b.get("cer_fijado"),
            "ruido": b.get("tasa_ruido"), "fuente": b.get("tea_fuente"),
            "precio": precio, "p1816": p1816,
            "tea_vista": _f(m.get("TEA")),
            "tea_guard": _f(s.get("tea")),
            "tea_motor": _f((r_motor or {}).get("TEA")),
            "tea_hoy": _f((r_hoy or {}).get("TEA")),
            "tea_su_px": _f((r_su_precio or {}).get("TEA")),
            "tea_1816": _f(o.get("tea")),
            "tna_1816": _f(o.get("tna")),
            "dur_vista": _f(m.get("duration")),
            "dur_motor": _f((r_motor or {}).get("duration")),
            "dur_1816": _f(o.get("duration")),
            "err": (r_motor or {}).get("_error"),
        })
    return filas


# ─────────────────────────────────────────────────────────────────────────────
def bloque_1(filas: list[dict]) -> None:
    print("\n" + "=" * 92)
    print("1. ¿LO GUARDADO CORRESPONDE A SU PROPIO PRECIO? (recálculo con la "
          "función del motor)")
    print("=" * 92)
    print("  Se vuelve a correr `engines.curvas.calcular_campos` con el MISMO")
    print("  precio y la MISMA fecha base que usó el motor. Si no coincide, el")
    print("  número de la base quedó de un cálculo anterior — el motor recalcula")
    print("  solo cuando el `last_price` cambia.\n")
    con_px = [f for f in filas if f["precio"]]
    desfasados = [f for f in con_px
                  if f["tea_motor"] is not None and f["tea_guard"] is not None
                  and abs(_dif(f["tea_motor"], f["tea_guard"]) or 0) > DIF_REPORTA]
    huerfanos = [f for f in con_px if f["tea_guard"] is None and f["tea_motor"] is not None]
    fantasmas = [f for f in con_px if f["tea_guard"] is not None and f["tea_motor"] is None]
    print(f"  bonos en la pill TASA FIJA ............... {len(filas)}")
    print(f"  con precio ............................... {len(con_px)}")
    print(f"  guardado ≠ recalculado (>{DIF_REPORTA} pp) ....... {len(desfasados)}")
    print(f"  el motor SÍ puede y la base está vacía ... {len(huerfanos)}")
    print(f"  la base tiene tasa y el motor NO puede ... {len(fantasmas)}"
          "   ← tasa fantasma")
    if desfasados or huerfanos or fantasmas:
        print(f"\n  {'TICKER':<9} {'PRECIO':>10} {'TNA BASE':>9} {'TNA RECALC':>11} "
              f"{'DIF pp':>8}")
        for f in (desfasados + huerfanos + fantasmas)[:30]:
            print(f"  {f['tk']:<9} {f['precio'] or 0:>10.2f} "
                  f"{_pct(_tna(f['tea_guard']), 1):>9} "
                  f"{_pct(_tna(f['tea_motor']), 1):>11} "
                  f"{(_dif(_tna(f['tea_motor']), _tna(f['tea_guard'])) or 0):>8.2f}")

    # El paso del calendario: mismo precio, fecha de hoy. Aísla cuánto del número
    # se mueve solo porque pasaron días, sin que el bono haya operado.
    calendario = [f for f in con_px
                  if f["tea_hoy"] is not None and f["tea_motor"] is not None
                  and abs(_dif(f["tea_hoy"], f["tea_motor"]) or 0) > DIF_REPORTA]
    print(f"\n  cambian si se los fecha HOY ............. {len(calendario)}"
          "   (mismo precio, otra fecha base)")
    for f in calendario[:10]:
        print(f"     {f['tk']:<9} con su fecha {_pct(_tna(f['tea_motor']), 1)} → "
              f"a hoy {_pct(_tna(f['tea_hoy']), 1)}")

    errs = [f for f in filas if f["err"]]
    if errs:
        print(f"\n  ⚠️  el recálculo LEVANTÓ EXCEPCIÓN en {len(errs)}:")
        for f in errs[:10]:
            print(f"     {f['tk']:<9} {f['err']}")


def bloque_2(filas: list[dict], fecha_1816: str | None) -> None:
    print("\n" + "=" * 92)
    print(f"2. CONTRA 1816 — mismo ticker, misma pata  (rueda de 1816: {fecha_1816 or '--'})")
    print("=" * 92)
    hay = [f for f in filas if f["tea_1816"] is not None]
    print(f"  bonos que 1816 cubre ..................... {len(hay)} de {len(filas)}")
    if not hay:
        print("  (sin cobertura: no hay nada que comparar)")
        return

    print("\n  La columna que importa es la ÚLTIMA: nuestra fórmula corrida con")
    print("  el precio de ELLOS. Si ahí la diferencia se va, el cálculo está bien")
    print("  y lo que difiere es el precio de arranque.\n")
    print(f"  {'TICKER':<9} {'PX NUES':>9} {'PX 1816':>9} {'Δ PX':>7}  "
          f"{'TNA NUES':>9} {'TNA 1816':>9} {'Δ pp':>7}  {'CON SU PX':>10} {'Δ pp':>7}")
    peores = sorted(
        hay, key=lambda f: -abs(_dif(_tna(f["tea_1816"]), _tna(f["tea_vista"])) or 0))
    for f in peores[:40]:
        dpx = (None if not (f["precio"] and f["p1816"])
               else (f["precio"] / f["p1816"] - 1) * 100)
        d_directa = _dif(_tna(f["tea_vista"]), _tna(f["tea_1816"]))
        d_su_px = _dif(_tna(f["tea_su_px"]), _tna(f["tea_1816"]))
        print(f"  {f['tk'] + ('·f' if f['cer_fijado'] else ''):<9} "
              f"{f['precio'] or 0:>9.2f} {f['p1816'] or 0:>9.2f} "
              f"{(dpx if dpx is not None else 0):>6.2f}%  "
              f"{_pct(_tna(f['tea_vista']), 1):>9} {_pct(_tna(f['tea_1816']), 1):>9} "
              f"{(d_directa if d_directa is not None else 0):>7.2f}  "
              f"{_pct(_tna(f['tea_su_px']), 1):>10} "
              f"{(d_su_px if d_su_px is not None else 0):>7.2f}")
    if len(peores) > 40:
        print(f"  … y {len(peores) - 40} más")

    # El veredicto, contado: de los que difieren, ¿a cuántos los explica el precio?
    fij = [f for f in hay if f["cer_fijado"]]
    print(f"\n  de los {len(hay)}, CER ya fijados (·f) ......... {len(fij)}")
    print("  ⚠️  Un CER fijado NO lo calcula la rama `tasa_fija` del motor sino la")
    print("      rama CER (`rama_calculo` decide por el AJUSTE, no por la pill).")
    print("      Comparten tabla y no comparten cuenta: si la diferencia se")
    print("      concentra en los ·f, el problema es el CER de liquidación, no")
    print("      la fórmula de tasa fija.")

    diff = [f for f in hay
            if abs(_dif(_tna(f["tea_vista"]), _tna(f["tea_1816"])) or 0) > DIF_REPORTA]
    expl = [f for f in diff if f["tea_su_px"] is not None
            and abs(_dif(_tna(f["tea_su_px"]), _tna(f["tea_1816"])) or 0) <= DIF_REPORTA]
    print(f"\n  difieren más de {DIF_REPORTA} pp ................... {len(diff)}")
    print(f"  … de esos, los explica EL PRECIO ........ {len(expl)}")
    print(f"  … NO los explica el precio .............. {len(diff) - len(expl)}"
          "   ← acá sí hay algo del cálculo")
    for f in diff:
        if f in expl:
            continue
        print(f"     {f['tk']:<9} con su precio {f['p1816']} daríamos "
              f"{_pct(_tna(f['tea_su_px']), 2)} y ellos dicen "
              f"{_pct(_tna(f['tea_1816']), 2)}")


def bloque_3(filas: list[dict]) -> None:
    print("\n" + "=" * 92)
    print("3. ¿LA TNA DE 1816 ES LA MISMA CONVENCIÓN QUE LA NUESTRA?")
    print("=" * 92)
    print("  La casa deriva TNA = TEM×12 con TEM = (1+TEA)^(1/12)−1. Se compara la")
    print("  `tna` que publica 1816 contra la que sale de SU PROPIA `tea`: si no")
    print("  cierran, las dos columnas no son comparables aunque el bono esté bien")
    print("  valuado, y esto NO depende de que nuestro precio coincida.\n")
    hay = [f for f in filas if f["tea_1816"] is not None and f["tna_1816"] is not None]
    if not hay:
        print("  1816 no devolvió `tna` para ningún bono — nada que verificar.")
        return
    malos = [f for f in hay
             if abs((f["tna_1816"] - (_tna(f["tea_1816"]) or 0)) * 100) > 0.02]
    print(f"  bonos con `tea` y `tna` de 1816 .......... {len(hay)}")
    print(f"  donde su TNA ≠ TEM×12 de su TEA .......... {len(malos)}")
    if malos:
        print(f"\n  {'TICKER':<9} {'TEA 1816':>9} {'TNA 1816':>9} {'TEM×12':>9} {'Δ pp':>7}")
        for f in malos[:20]:
            der = _tna(f["tea_1816"])
            print(f"  {f['tk']:<9} {_pct(f['tea_1816'], 2):>9} {_pct(f['tna_1816'], 2):>9} "
                  f"{_pct(der, 2):>9} {((f['tna_1816'] - (der or 0)) * 100):>7.2f}")
        print("\n  ⚠️  Con esto, comparar «nuestra TNA» contra «la TNA de ellos» mide")
        print("      DOS cosas a la vez. La comparación válida es TEA contra TEA.")
    else:
        print("  ✅ misma convención: las dos columnas TNA son comparables.")


def bloque_4(filas: list[dict]) -> None:
    print("\n" + "=" * 92)
    print("4. DURATION — el eje X del gráfico y el filtro de tasa RUIDO")
    print("=" * 92)
    sin = [f for f in filas if f["tea_vista"] is not None and f["dur_vista"] is None]
    dif = [f for f in filas if f["dur_vista"] and f["dur_1816"]
           and abs(f["dur_vista"] - f["dur_1816"]) > 0.05]
    print(f"  con tasa y SIN duration .................. {len(sin)}"
          "   (no se grafican y no pasan por el filtro de ruido)")
    for f in sin[:10]:
        print(f"     {f['tk']:<9} TNA {_pct(_tna(f['tea_vista']), 1)}")
    print(f"\n  duration nuestra vs 1816, dif > 0,05 ..... {len(dif)}")
    for f in dif[:15]:
        print(f"     {f['tk']:<9} nuestra {f['dur_vista']:.3f}  1816 {f['dur_1816']:.3f}")


def detalle(tk: str) -> int:
    print("\n" + "=" * 92)
    print(f"DETALLE — {tk}")
    print("=" * 92)
    bonos = [b for b in universo() if b["ticker_corto"].upper() == tk.upper()]
    if not bonos:
        print(f"  {tk} no está en la pill TASA FIJA de la vista.")
        return 1
    b = bonos[0]
    ins = motor_insumos()
    snaps = snapshot_de([b.get("instrumento")])
    d1816, fecha = pedir_1816([b["ticker_corto"]])
    f = auditar([b], ins, snaps, d1816)[0]

    inst = ins["curvas"].get(b.get("instrumento")) or {}
    flujos = inst.get("flujos") or []
    print(f"  símbolo de mercado ....... {b.get('instrumento')}")
    print(f"  CER fijado ............... {b.get('cer_fijado')}")
    print(f"  fuente de la tasa ........ {b.get('tea_fuente') or 'motor (live)'}")
    print(f"  flujos en el master ...... {len(flujos)}")
    print(f"  flujo_vencimiento ........ {inst.get('flujo_vencimiento')}")
    print(f"  vencimiento .............. {inst.get('fecha_vencimiento')}")
    print(f"\n  PRECIO      nuestro {f['precio']}   ·   1816 {f['p1816']}")
    print(f"\n  {'DE DÓNDE SALE':<34} {'TEA':>9} {'TNA':>9}")
    for etiq, tea in (("lo que muestra la pantalla", f["tea_vista"]),
                      ("lo guardado en el snapshot", f["tea_guard"]),
                      ("recalculado (fecha del motor)", f["tea_motor"]),
                      ("recalculado (fechado hoy)", f["tea_hoy"]),
                      ("recalculado CON EL PRECIO DE 1816", f["tea_su_px"]),
                      ("lo que dice 1816", f["tea_1816"])):
        print(f"  {etiq:<34} {_pct(tea, 2):>9} {_pct(_tna(tea), 2):>9}")
    if f["tna_1816"] is not None:
        print(f"  {'la TNA que publica 1816':<34} {'':>9} {_pct(f['tna_1816'], 2):>9}")
    print(f"\n  duration: pantalla {f['dur_vista']}  ·  recalculada {f['dur_motor']}"
          f"  ·  1816 {f['dur_1816']}")
    print(f"  rueda de 1816: {fecha or '--'}")
    if f["err"]:
        print(f"  ⚠️  el recálculo levantó: {f['err']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-1816", action="store_true",
                    help="solo el recálculo local (no gasta créditos)")
    ap.add_argument("--dry", action="store_true",
                    help="universo y costo en créditos, sin pegarle a 1816")
    ap.add_argument("--ticker", help="detalle de UN bono, paso por paso")
    args = ap.parse_args()

    if args.ticker:
        return detalle(args.ticker)

    bonos = universo()
    tickers = [b["ticker_corto"] for b in bonos]
    if args.dry:
        g = grafias(tickers)
        print(f"\n  bonos en TASA FIJA ....... {len(bonos)}")
        print(f"  grafías a pedirle a 1816 . {len(g)}")
        print(f"  campos ................... {len(CAMPOS_1816)} {CAMPOS_1816}")
        print(f"  costo estimado ........... {len(g) * len(CAMPOS_1816)} créditos\n")
        for grafia, nuestro in sorted(g.items())[:60]:
            print(f"     {nuestro:<9} → {grafia}")
        return 0

    print(f"\n  Universo: {len(bonos)} bonos en la pill TASA FIJA.")
    print("  Cargando los insumos del motor (curvas, CER, días hábiles, MEP)…")
    ins = motor_insumos()
    snaps = snapshot_de([b.get("instrumento") for b in bonos if b.get("instrumento")])

    d1816, fecha = ({}, None)
    if not args.sin_1816:
        print("  Pidiéndole a 1816 la tasa de los mismos tickers…")
        d1816, fecha = pedir_1816(tickers)

    filas = auditar(bonos, ins, snaps, d1816)
    bloque_1(filas)
    if d1816:
        bloque_2(filas, fecha)
        bloque_3(filas)
    bloque_4(filas)
    print("\n" + "=" * 92)
    print("LEER ASÍ: el bloque 1 dice si el número de la base es el que sale de")
    print("recalcularlo (problema NUESTRO). El 2 dice si lo que queda difiriendo")
    print("contra 1816 lo explica el precio o no. El 3 avisa si las dos columnas")
    print("TNA ni siquiera son la misma cuenta. Sin el 3 en verde, el 2 se lee")
    print("solo por la columna de TEA.")
    print("=" * 92 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
