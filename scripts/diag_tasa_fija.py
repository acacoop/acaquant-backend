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
  Primary (live); 1816 publica el suyo de BYMA. Con precios distintos las tasas
  TIENEN que dar distinto y no hay nada roto. Por eso el script recalcula
  **nuestra fórmula con el precio de ELLOS**: si ahí da su TEA, el cálculo está
  bien y la diferencia era el precio. Evita salir a tocar un motor que está sano.

  ⚠️ El precio comparable es **`precioDirty`**, no `precioClean` (corregido
  2026-09-02, tras verlo en la corrida). `precioClean` está expresado sobre el
  VALOR TÉCNICO: para las letras capitalizables da ~100 siempre, y el cociente
  contra nuestro precio de mercado es exactamente lo que capitalizó el papel
  (S15S6 1,064 · S30O6 1,285 · T15E7 1,474). Metérselo a una fórmula que espera
  el precio sucio por 100 VN daba TNAs de 200% que no eran un hallazgo: eran
  este bug.

  Y para no depender de la comparación en absoluto está `--cruzado`, que usa el
  endpoint de INPUT MANUAL de 1816 (`indicadores_de`): se le pasa NUESTRO precio
  y devuelve SU tasa sobre el MISMO número. Ahí la variable «precio» desaparece
  y lo que queda es convención pura. Cuesta campos por ticker, aparte.

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
    python -m scripts.diag_tasa_fija --cruzado      # + su tasa sobre NUESTRO precio
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


# `convencionTna` y `fechaLiquidacion` son METADATA (`mercado_1816.CAMPOS_METADATA`):
# vienen llenas aunque no haya precio, y `indicadores_vigentes` ya las excluye del
# predicado de «esta rueda trajo datos» — sin eso el retroceso no correría nunca.
#
# `convencionTna` es EL campo de esta auditoría: 1816 DECLARA con qué convención
# calculó su TNA. Sin él había que deducirla despejando la fórmula de los números;
# con él la pregunta la contesta el proveedor.
CAMPOS_1816 = ["tea", "tna", "tem", "duration", "precioDirty", "precioClean",
               "convencionTna", "fechaLiquidacion"]


def pedir_1816(tickers: list[str]) -> tuple[dict[str, dict], str | None]:
    """`nuestro ticker → sus indicadores` + la rueda que salió.

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
        if v.get("tea") is None and v.get("precioDirty") is None:
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
        # El de MERCADO. `precioClean` va aparte porque es sobre valor técnico y
        # solo sirve para mostrar cuánto capitalizó el papel, no para recalcular.
        p1816 = _f(o.get("precioDirty"))
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
            "tem_1816": _f(o.get("tem")),
            "convencion": o.get("convencionTna"),
            "liq_1816": o.get("fechaLiquidacion"),
            "clean_1816": _f(o.get("precioClean")),
            "vto": b.get("vencimiento"),
            "cruzado": None,          # lo llena --cruzado
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


def _dias(fila) -> int | None:
    """Días de liquidación a vencimiento, con las fechas de 1816 cuando las da.

    La `fechaLiquidacion` sale de ELLOS a propósito: si se asumiera T+1 nuestro y
    ellos liquidaran distinto, la fórmula candidata fallaría por el plazo y
    parecería que la convención es otra. El insumo tiene que ser el de ellos.
    """
    from datetime import date
    v, liq = fila.get("vto"), fila.get("liq_1816")
    if not v or not liq:
        return None
    try:
        a = date.fromisoformat(str(v)[:10])
        b = date.fromisoformat(str(liq)[:10])
    except ValueError:
        return None
    d = (a - b).days
    return d if d > 0 else None


def bloque_3(filas: list[dict]) -> None:
    print("\n" + "=" * 92)
    print("3. LA CONVENCIÓN DE LA TNA — la declarada por 1816 y las tres candidatas")
    print("=" * 92)
    hay = [f for f in filas if f["tea_1816"] is not None and f["tna_1816"] is not None]
    if not hay:
        print("  1816 no devolvió `tna` para ningún bono — nada que verificar.")
        return

    # LO QUE 1816 DICE DE SÍ MISMO. Antes esto se deducía despejando la fórmula;
    # el campo existía en el enum del spec y no se pedía.
    decl = {}
    for f in hay:
        decl[f["convencion"] or "(no la declaró)"] = decl.get(
            f["convencion"] or "(no la declaró)", 0) + 1
    print("\n  ► CONVENCIÓN QUE DECLARA 1816 (campo `convencionTna`):")
    for c, n in sorted(decl.items(), key=lambda x: -x[1]):
        print(f"       {c!r} — en {n} de {len(hay)} bonos")

    # Y LA VERIFICACIÓN, que no depende de que el campo venga ni de creerle:
    # se reconstruye su TNA con las tres candidatas y gana la que la reproduce.
    print("\n  ► LAS TRES CANDIDATAS, reconstruidas desde SU PROPIA `tea`:")
    print("     · LINEAL 365 = (rendimiento del plazo) × 365/días")
    print("     · TEM×12     = la que deriva nuestra pantalla hoy")
    print("     · EFECTIVA   = la TIR misma (TNA = TEA)")
    print(f"\n  {'TICKER':<9}{'DÍAS':>6}{'TNA 1816':>10}{'LINEAL':>9}{'TEM×12':>9}"
          f"{'EFECTIVA':>10}   GANA")
    puntos = {"lineal": 0, "temx12": 0, "efectiva": 0, "ninguna": 0}
    err = {"lineal": [], "temx12": [], "efectiva": []}
    for f in sorted(hay, key=lambda x: _dias(x) or 0):
        tea, tna, d = f["tea_1816"], f["tna_1816"], _dias(f)
        lineal = (((1 + tea) ** (d / 365) - 1) * 365 / d) if d else None
        cands = {"lineal": lineal, "temx12": _tna(tea), "efectiva": tea}
        for k, v in cands.items():
            if v is not None:
                err[k].append(abs(v - tna) * 100)
        vivas = {k: abs(v - tna) * 100 for k, v in cands.items() if v is not None}
        gana = min(vivas, key=vivas.get) if vivas else None
        puntos[gana if gana and vivas[gana] <= 0.05 else "ninguna"] += 1
        print(f"  {f['tk']:<9}{d or 0:>6}{_pct(tna, 2):>10}{_pct(lineal, 2):>9}"
              f"{_pct(cands['temx12'], 2):>9}{_pct(cands['efectiva'], 2):>10}   "
              f"{gana if gana and vivas[gana] <= 0.05 else '—'}")

    print("\n  ► VEREDICTO (una candidata «gana» un bono si la reproduce a ≤0,05 pp)")
    for k in ("lineal", "temx12", "efectiva"):
        e = err[k]
        peor = f"{max(e):.2f} pp" if e else "--"
        print(f"       {k:<9} gana en {puntos[k]:>2} de {len(hay)}   ·   "
              f"peor error {peor}")
    if puntos["ninguna"]:
        print(f"       ninguna   en {puntos['ninguna']} — ahí la convención es OTRA")

    print("\n  ► Y LA NUESTRA, contra la ganadora:")
    print("     la pantalla deriva TEM×12. Cuánto se aparta, por plazo — si esto")
    print("     crece ordenado con los días, es diferencia de FÓRMULA y no un bug.")
    for f in sorted(hay, key=lambda x: _dias(x) or 0):
        d = _dias(f)
        nuestra = _tna(f["tea_vista"])
        if nuestra is None or d is None:
            continue
        print(f"     {f['tk']:<9}{d:>5}d   nuestra {_pct(nuestra, 2)}   "
              f"1816 {_pct(f['tna_1816'], 2)}   dif "
              f"{((nuestra - f['tna_1816']) * 100):>6.2f} pp")


def bloque_cruzado(filas: list[dict]) -> None:
    """SU tasa sobre NUESTRO precio. Elimina la variable del precio.

    Es la respuesta a «no es justo comparar, los precios son de momentos
    distintos»: acá el precio es UNO SOLO y es el nuestro, así que lo que quede
    de diferencia no puede ser el insumo.
    """
    print("\n" + "=" * 92)
    print("5. CONTROL CRUZADO — la tasa de 1816 calculada sobre NUESTRO precio")
    print("=" * 92)
    hechos = [f for f in filas if f.get("cruzado")]
    if not hechos:
        print("  (sin datos: correr con --cruzado)")
        return
    print(f"\n  {'TICKER':<9}{'PRECIO':>10}{'TEA NUESTRA':>12}{'TEA DE ELLOS':>13}"
          f"{'dif pp':>8}   {'TNA NUESTRA':>12}{'TNA DE ELLOS':>13}{'dif pp':>8}")
    for f in hechos:
        c = f["cruzado"]
        te, tn = _f(c.get("tea")), _f(c.get("tna"))
        dt_ = _dif(f["tea_vista"], te)
        dn = _dif(_tna(f["tea_vista"]), tn)
        print(f"  {f['tk']:<9}{f['precio'] or 0:>10.2f}{_pct(f['tea_vista'], 2):>12}"
              f"{_pct(te, 2):>13}{(dt_ or 0):>8.2f}   "
              f"{_pct(_tna(f['tea_vista']), 2):>12}{_pct(tn, 2):>13}{(dn or 0):>8.2f}")
    print("\n  Con el MISMO precio: si la columna TEA cierra y la TNA no, no hay")
    print("  nada mal valuado — son dos convenciones distintas y punto.")


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


def cruzar(filas: list[dict]) -> None:
    """Le pide a 1816 SUS indicadores calculados sobre NUESTRO precio.

    Una llamada por ticker (el endpoint es `/indicadores/{ticker}`), y cuesta
    CAMPOS por llamada, no tickers × campos. Se le manda `precioDirty` porque es
    el precio de mercado — el mismo tipo de número que nuestro `last_price`.
    """
    from core import mercado_1816
    if not mercado_1816.disponible():
        return
    for f in filas:
        if not f["precio"] or f["tea_1816"] is None:
            continue
        try:
            r = mercado_1816.indicadores_de(
                f["tk"], ["tea", "tna", "tem", "duration"],
                precioDirty=float(f["precio"]))
        except Exception as e:
            print(f"     {f['tk']}: 1816 rechazó el cruce ({str(e)[:90]})")
            continue
        # La respuesta trae el ticker adentro de `instrumentos`, igual que el
        # endpoint masivo; algunas versiones la devuelven plana.
        inst = (r.get("instrumentos") or {})
        f["cruzado"] = next(iter(inst.values()), None) or (
            r if r.get("tea") is not None else None)


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
    ap.add_argument("--cruzado", action="store_true",
                    help="pide la tasa de 1816 sobre NUESTRO precio (elimina la "
                         "variable del precio). Cuesta campos POR TICKER.")
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
    if args.cruzado and d1816:
        print("  Cruzando: la tasa de 1816 sobre NUESTRO precio…")
        cruzar(filas)
    bloque_1(filas)
    if d1816:
        bloque_2(filas, fecha)
        bloque_3(filas)
    bloque_4(filas)
    if args.cruzado:
        bloque_cruzado(filas)
    print("\n" + "=" * 92)
    print("LEER ASÍ, y en este orden:")
    print("  · Bloque 1 — ¿el número de la base es el que sale de recalcularlo?")
    print("    Si acá hay algo, el problema es NUESTRO y no hay que mirar más.")
    print("  · Bloque 3 — ¿las dos columnas TNA son siquiera la misma cuenta?")
    print("    Va ANTES del 2 en la lectura: si la convención difiere, comparar")
    print("    TNA contra TNA mide dos cosas a la vez y no significa nada.")
    print("  · Bloque 2 — con la convención ya despejada, la comparación válida")
    print("    es TEA contra TEA, y la última columna dice si lo que sobra lo")
    print("    explica el precio.")
    print("  · --cruzado — la misma comparación con UN SOLO precio, el nuestro.")
    print("    Es lo que hay que correr cuando la duda es «las fotos son de")
    print("    momentos distintos»: ahí no hay dos momentos.")
    print("=" * 92 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
