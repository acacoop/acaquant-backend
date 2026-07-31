"""scripts/qa_informe.py — QA de la vista NEGOCIO → OPERADORES → INFORME contra la base.

Valida que los 4 cuadrantes del Informe devuelvan EXACTAMENTE lo que dice la base de
datos, recomputando cada número por un CAMINO INDEPENDIENTE (SQL directo, sin pasar por
`_rollup_por_cuenta` ni por la lógica del service). Si el service tiene un bug de
ventanas (DESDE/HASTA/MES), de filtros o de agrupación, los dos caminos divergen y el
check da FAIL con el detalle de la diferencia.

Reglas de negocio validadas (las mismas del service, recomputadas a mano):
  - Arancel: tabla `operaciones`, `arancel > 0`, `etapa IS DISTINCT FROM 'solicitud'`,
    fecha = `concertacion`. TOTAL = [desde, hasta]; MES = [1º del mes del HASTA, hasta].
  - Volumen: tabla `negocio_movimientos`, categorías de volumen, `unidad != 'USDL'`,
    pesificación por MEP del boleto.
  - Solo cuentas presentes en `comitentes` con estado='Activa'.
  - CTAS OPS = cuentas DISTINTAS con >=1 op de volumen en el mes del corte.
  - Filtros madre (division) particionan: ESTE + OESTE + sin-división = global.

Escenarios: (a) histórico hasta hoy, (b) mes actual [1º, hoy], (c) mes pasado completo
(valida que el HASTA en el pasado NO arrastre operaciones posteriores).

Batería 3 amplía a TODAS las vistas comerciales: Informe con DESDE/HASTA genéricos
(¿el ARANCEL MES toma el mes calendario del HASTA?), Control Comercial (Tabla 1 períodos
fijos + Tabla 2 por operador con activos/inactivos y % vs período anterior) y Análisis
Comercial (estados ACTIVA/ENFRIANDOSE/DORMIDA/NUEVA, última operación, flags MTD/YTD).

Uso (Droplet): python -m scripts.qa_informe
Read-only (solo SELECT). Exit code 1 si algún check falla.
"""
from __future__ import annotations

import calendar
import sys
from datetime import date, timedelta

from api.services import comercial_sql as cs
from api.services import control_comercial_sql as ccs
from api.services._sql import _f, _q
from api.services.comercial import _CATS_VOLUMEN, _hoy_art

_TOL = 0.05  # tolerancia por redondeo a 2 decimales en agregados

_FAILS: list[str] = []


def _check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {nombre}" + (f" — {detalle}" if detalle else ""))
    if not ok:
        _FAILS.append(nombre)


def _cmp_maps(nombre: str, esperado: dict[str, float], obtenido: dict[str, float]) -> None:
    """Compara dos dicts clave→valor con tolerancia; lista las claves que difieren."""
    difs = []
    for k in sorted(set(esperado) | set(obtenido)):
        e, o = esperado.get(k, 0.0), obtenido.get(k, 0.0)
        if abs(e - o) > _TOL:
            difs.append(f"{k}: base={e:,.2f} vs informe={o:,.2f} (dif {o - e:+,.2f})")
    _check(nombre, not difs, "; ".join(difs[:6]) + (f" (+{len(difs) - 6} más)" if len(difs) > 6 else ""))


# ── Recomputo INDEPENDIENTE (SQL directo, sin _rollup_por_cuenta) ──────────────

_OP_KEY = "COALESCE(NULLIF(lower(trim(c.operador_email)), ''), '(sin operador)')"


def _ar_por_operador(desde: str | None, hasta: str | None, division=None) -> dict[str, float]:
    """Arancel por operador en [desde, hasta] — camino independiente (JOIN directo)."""
    w, p = "o.arancel > 0 AND o.etapa IS DISTINCT FROM 'solicitud'", {}
    if desde:
        w += " AND o.concertacion >= %(d)s"
        p["d"] = desde
    if hasta:
        w += " AND o.concertacion <= %(h)s"
        p["h"] = hasta
    if division:
        w += " AND c.division = ANY(%(div)s)"
        p["div"] = list(division)
    return {r["op"]: _f(r["ar"]) for r in _q(
        f"SELECT {_OP_KEY} AS op, SUM(o.arancel) AS ar "
        f"FROM operaciones o JOIN comitentes c ON c.id_cuenta = o.id_cuenta "
        f"AND c.estado = 'Activa' WHERE {w} GROUP BY 1", p)}


def _vol_por_operador(desde: str | None, hasta: str | None) -> dict[str, float]:
    """Volumen pesificado por operador en [desde, hasta] — camino independiente."""
    pesif = ("CASE WHEN nm.moneda = 'ARS' THEN abs(COALESCE(nm.importe, 0)) "
             "ELSE abs(COALESCE(nm.importe, 0)) * COALESCE(nm.mep, 0) END")
    w, p = "nm.categoria = ANY(%(cats)s) AND nm.unidad IS DISTINCT FROM 'USDL'", \
        {"cats": list(_CATS_VOLUMEN)}
    if desde:
        w += " AND nm.fecha >= %(d)s"
        p["d"] = desde
    if hasta:
        w += " AND nm.fecha <= %(h)s"
        p["h"] = hasta
    return {r["op"]: _f(r["vol"]) for r in _q(
        f"SELECT {_OP_KEY} AS op, SUM({pesif}) AS vol "
        f"FROM negocio_movimientos nm JOIN comitentes c ON c.id_cuenta = nm.id_cuenta "
        f"AND c.estado = 'Activa' WHERE {w} GROUP BY 1", p)}


def _ctas_ops_por_operador(mes_ini: str, hasta: str | None) -> dict[str, float]:
    """Cuentas DISTINTAS que operaron (>=1 op de volumen) en [mes_ini, hasta]."""
    w, p = ("nm.categoria = ANY(%(cats)s) AND nm.unidad IS DISTINCT FROM 'USDL' "
            "AND nm.fecha >= %(m)s"), {"cats": list(_CATS_VOLUMEN), "m": mes_ini}
    if hasta:
        w += " AND nm.fecha <= %(h)s"
        p["h"] = hasta
    return {r["op"]: float(r["n"]) for r in _q(
        f"SELECT {_OP_KEY} AS op, COUNT(DISTINCT nm.id_cuenta) AS n "
        f"FROM negocio_movimientos nm JOIN comitentes c ON c.id_cuenta = nm.id_cuenta "
        f"AND c.estado = 'Activa' WHERE {w} GROUP BY 1", p)}


def _cuentas_por_segmento(corte: str) -> dict[str, float]:
    return {r["seg"]: float(r["n"]) for r in _q(
        "SELECT COALESCE(nivel_1, '(sin segmentar)') AS seg, count(*) AS n "
        "FROM comitentes WHERE estado = 'Activa' AND fecha_alta_legajo <= %(c)s "
        "GROUP BY 1", {"c": corte})}


# ── Escenario: corre TODOS los checks para un (desde, hasta) dado ──────────────

def _escenario(titulo: str, desde: str | None, hasta: str | None) -> None:
    corte = date.fromisoformat(hasta) if hasta else date.today()
    mes_ini = corte.replace(day=1).isoformat()
    print(f"\n{'=' * 78}\nESCENARIO: {titulo} · desde={desde or '(histórico)'} · "
          f"hasta={hasta or '(hoy)'} · MES=[{mes_ini}, {hasta or 'hoy'}]\n{'=' * 78}")

    inf = cs.informe_comercial(moneda="ARS", fecha=hasta, desde=desde)
    rank = {(c["operador_email"] or "").strip().lower() or "(sin operador)": c
            for c in inf["comerciales"]}

    # 1) Arancel TOTAL por operador vs SQL directo.
    _cmp_maps("Q2 arancel TOTAL por operador == base",
              _ar_por_operador(desde, hasta),
              {k: c["ar_total"] for k, c in rank.items() if c["ar_total"]})

    # 2) Arancel MES por operador vs SQL directo (ventana mes calendario del HASTA).
    _cmp_maps("Q2 arancel MES por operador == base",
              _ar_por_operador(mes_ini, hasta),
              {k: c["ar_mes"] for k, c in rank.items() if c["ar_mes"]})

    # 3) Volumen TOTAL por operador vs SQL directo.
    _cmp_maps("Q2 volumen TOTAL por operador == base",
              _vol_por_operador(desde, hasta),
              {k: c["vol_total"] for k, c in rank.items() if c["vol_total"]})

    # 4) CTAS OPS por operador vs SQL directo.
    _cmp_maps("Q2 ctas_ops (mes del corte) por operador == base",
              _ctas_ops_por_operador(mes_ini, hasta),
              {k: float(c["ctas_ops"]) for k, c in rank.items() if c["ctas_ops"]})

    # 5) Q1: cuentas por segmento vs count directo de comitentes.
    q1 = cs.informe_cuentas_por_segmento(fecha=hasta or date.today().isoformat())
    _cmp_maps("Q1 cuentas por segmento == comitentes (alta <= corte)",
              _cuentas_por_segmento(hasta or date.today().isoformat()),
              {s["segmento"]: float(s["n"]) for s in q1["segmentos"]})

    # 6) Coherencia interna Q2 <-> Q3: mismo arancel total agrupado distinto.
    tot_rank = sum(c["ar_total"] for c in inf["comerciales"])
    tot_segs = sum(s["ar_total"] for s in inf["aranceles_segmento"])
    _check("Q2 sum(ar_total ranking) == Q3 sum(ar_total segmentos)",
           abs(tot_rank - tot_segs) <= _TOL,
           f"ranking={tot_rank:,.2f} vs segmentos={tot_segs:,.2f}")

    # 7) Q4 ('todos'): suma clientes == suma de las operaciones listadas == base.
    det = cs.informe_segmento_detalle(segmento="todos", moneda="ARS", fecha=hasta, desde=desde)
    sum_cli = sum(c["arancel_total"] for c in det["clientes"])
    sum_ops = sum(o["arancel"] for o in det["operaciones"])
    _check("Q4 sum(arancel_total clientes) == sum(arancel operaciones)",
           abs(sum_cli - sum_ops) <= max(_TOL, 0.01 * len(det["operaciones"])),
           f"clientes={sum_cli:,.2f} vs ops={sum_ops:,.2f}")
    base_total = sum(_ar_por_operador(desde, hasta).values())
    _check("Q4 sum(arancel_total clientes) == base (total del período)",
           abs(sum_cli - base_total) <= _TOL,
           f"clientes={sum_cli:,.2f} vs base={base_total:,.2f}")

    # 8) Respeto del HASTA: ninguna operación listada en Q4 es posterior al corte.
    if hasta:
        fuera = [o for o in det["operaciones"] if o["fecha"] and o["fecha"] > hasta]
        _check("Q4 ninguna operación posterior al HASTA", not fuera,
               f"{len(fuera)} ops fuera de rango (ej. {fuera[0]['fecha'] if fuera else ''})")
    if desde:
        antes = [o for o in det["operaciones"] if o["fecha"] and o["fecha"] < desde]
        _check("Q4 ninguna operación anterior al DESDE", not antes,
               f"{len(antes)} ops fuera de rango (ej. {antes[0]['fecha'] if antes else ''})")


def _particion_division(desde: str | None, hasta: str | None) -> None:
    """El filtro DIVISION particiona: sum(por división) + sin-división = global."""
    print(f"\n{'=' * 78}\nCHECK PARTICIÓN DIVISION (desde={desde}, hasta={hasta})\n{'=' * 78}")
    divs = [r["division"] for r in _q(
        "SELECT DISTINCT division FROM comitentes WHERE division IS NOT NULL "
        "AND estado = 'Activa'")]
    print(f"  divisiones en la base: {divs}")
    glob = sum(c["ar_total"] for c in
               cs.informe_comercial(moneda="ARS", fecha=hasta, desde=desde)["comerciales"])
    por_div = 0.0
    for d in divs:
        v = sum(c["ar_total"] for c in cs.informe_comercial(
            moneda="ARS", fecha=hasta, desde=desde, division=(d,))["comerciales"])
        print(f"    division={d!r}: ar_total={v:,.2f}")
        por_div += v
    # Cuentas SIN división (division IS NULL) quedan fuera de todo filtro → se computan
    # por el camino independiente: base global - base de cada división.
    base_glob = sum(_ar_por_operador(desde, hasta).values())
    base_divs = sum(sum(_ar_por_operador(desde, hasta, division=(d,)).values()) for d in divs)
    sin_div = base_glob - base_divs
    _check("DIVISION particiona (divisiones + sin-división == global)",
           abs((por_div + sin_div) - glob) <= _TOL,
           f"divisiones={por_div:,.2f} + sin_div={sin_div:,.2f} vs global={glob:,.2f}")


# ── BATERÍA 2: drill-downs, cuenta individual, moneda, cortes intra-mes ────────

def _bateria2(desde: str, hasta: str) -> None:
    print(f"\n{'=' * 78}\nBATERÍA 2 — drill-down / cuenta / moneda / cortes (desde={desde}, hasta={hasta})\n{'=' * 78}")
    inf = cs.informe_comercial(moneda="ARS", fecha=hasta, desde=desde)
    comerciales = inf["comerciales"]
    if not comerciales:
        _check("hay comerciales en el período", False, "informe vacío — no se puede testear")
        return
    top = comerciales[0]
    top_email = top["operador_email"]

    # B1) Ranking ordenado DESC por vol_total y ranks contiguos 1..N.
    vols = [c["vol_total"] for c in comerciales]
    _check("B1 ranking ordenado desc por vol_total", vols == sorted(vols, reverse=True))
    _check("B1 ranks contiguos 1..N",
           [c["rank"] for c in comerciales] == list(range(1, len(comerciales) + 1)))

    # B2) ticket_promedio == vol_total / n_ops (recomputado).
    difs = [c["operador_nombre"] for c in comerciales if c["n_ops"]
            and abs(c["ticket_promedio"] - round(c["vol_total"] / c["n_ops"], 2)) > _TOL]
    _check("B2 ticket_promedio == vol_total/n_ops en el ranking", not difs, ", ".join(difs[:4]))

    # B3) Drill-down Q3 por comercial: la suma de sus segmentos == su fila del ranking.
    q3 = cs.informe_aranceles_segmento(operador=top_email, moneda="ARS",
                                       fecha=hasta, desde=desde)
    s3 = sum(s["ar_total"] for s in q3["aranceles_segmento"])
    _check(f"B3 Q3 drill-down de {top['operador_nombre']!r}: sum(segmentos) == fila ranking",
           abs(s3 - top["ar_total"]) <= _TOL, f"drill={s3:,.2f} vs ranking={top['ar_total']:,.2f}")

    # B4) Q4 drill-down por operador: sum(clientes) == fila del ranking.
    d4 = cs.informe_segmento_detalle(segmento="todos", operador=top_email, moneda="ARS",
                                     fecha=hasta, desde=desde)
    s4 = sum(c["arancel_total"] for c in d4["clientes"])
    _check(f"B4 Q4 drill-down de {top['operador_nombre']!r}: sum(clientes) == fila ranking",
           abs(s4 - top["ar_total"]) <= _TOL, f"drill={s4:,.2f} vs ranking={top['ar_total']:,.2f}")

    # B5) Q4 por segmento: sum(clientes del segmento) == fila de Q3 global.
    segs_glob = {s["segmento"]: s["ar_total"] for s in inf["aranceles_segmento"]}
    seg_top = max(segs_glob, key=lambda s: segs_glob[s])
    d5 = cs.informe_segmento_detalle(segmento=seg_top, moneda="ARS", fecha=hasta, desde=desde)
    s5 = sum(c["arancel_total"] for c in d5["clientes"])
    _check(f"B5 Q4 segmento {seg_top!r}: sum(clientes) == Q3 global",
           abs(s5 - segs_glob[seg_top]) <= _TOL,
           f"detalle={s5:,.2f} vs Q3={segs_glob[seg_top]:,.2f}")

    # B6) Q4 '(sin segmentar)': todos los clientes tienen nivel_1 NULL en la base.
    d6 = cs.informe_segmento_detalle(segmento="(sin segmentar)", moneda="ARS",
                                     fecha=hasta, desde=desde)
    ids6 = [c["id_cuenta"] for c in d6["clientes"]]
    mal = _q("SELECT id_cuenta FROM comitentes WHERE id_cuenta = ANY(%(i)s) "
             "AND nivel_1 IS NOT NULL", {"i": ids6}) if ids6 else []
    _check("B6 Q4 '(sin segmentar)' → solo cuentas con nivel_1 NULL", not mal,
           f"{len(mal)} cuentas con segmento asignado")

    # B7) CUENTA individual: el arancel de la cuenta top en Q4 == SQL directo.
    if d4["clientes"]:
        cta = d4["clientes"][0]
        base_cta = _f(_q(
            "SELECT COALESCE(SUM(arancel), 0) AS ar FROM operaciones "
            "WHERE id_cuenta = %(c)s AND arancel > 0 AND etapa IS DISTINCT FROM 'solicitud' "
            "AND concertacion >= %(d)s AND concertacion <= %(h)s",
            {"c": cta["id_cuenta"], "d": desde, "h": hasta})[0]["ar"])
        _check(f"B7 cuenta {cta['id_cuenta']}: arancel Q4 == SQL directo",
               abs(cta["arancel_total"] - base_cta) <= _TOL,
               f"Q4={cta['arancel_total']:,.2f} vs base={base_cta:,.2f}")

    # B8) MES no depende del DESDE: ar_mes idéntico con desde=mes vs desde=histórico.
    inf_hist = cs.informe_comercial(moneda="ARS", fecha=hasta, desde=None)
    mes_a = {c["operador_email"]: c["ar_mes"] for c in comerciales}
    mes_b = {c["operador_email"]: c["ar_mes"] for c in inf_hist["comerciales"]}
    difs8 = [k for k in set(mes_a) | set(mes_b)
             if abs(mes_a.get(k, 0) - mes_b.get(k, 0)) > _TOL]
    _check("B8 ar_mes independiente del DESDE (mes vs histórico)", not difs8,
           f"{len(difs8)} operadores difieren")

    # B9) Monotonía: ar_total histórico >= ar_total del mes, por operador.
    tot_h = {c["operador_email"]: c["ar_total"] for c in inf_hist["comerciales"]}
    difs9 = [k for k, v in mes_a.items() if tot_h.get(k, 0) + _TOL < v]
    _check("B9 ar_total histórico >= ar_mes por operador", not difs9,
           f"{len(difs9)} operadores con mes > histórico")

    # B10) Corte INTRA-MES (día 15): ventana MES = [1º, 15] — nada después del 15.
    corte15 = date.fromisoformat(hasta).replace(day=15).isoformat()
    inf15 = cs.informe_comercial(moneda="ARS", fecha=corte15, desde=desde)
    base15 = _ar_por_operador(corte15[:8] + "01", corte15)
    _cmp_maps("B10 corte intra-mes (día 15): ar_mes == base [1º, 15]",
              base15,
              {(c["operador_email"] or "").strip().lower() or "(sin operador)": c["ar_mes"]
               for c in inf15["comerciales"] if c["ar_mes"]})

    # B11) MONEDA: informe USD == informe ARS / MEP (misma foto, factor uniforme).
    # Solo operadores con >100 USD (el redondeo a 2 decimales distorsiona los ratios chicos).
    inf_usd = cs.informe_comercial(moneda="USD", fecha=hasta, desde=desde)
    usd = {c["operador_email"]: c["ar_total"] for c in inf_usd["comerciales"]}
    ars = {c["operador_email"]: c["ar_total"] for c in comerciales}
    pares = [(k, ars[k] / usd[k]) for k in usd if usd.get(k, 0) > 100 and ars.get(k)]
    meps = [m for _, m in pares]
    _check("B11 moneda: ARS/USD da el MISMO MEP para todos los operadores",
           bool(meps) and (max(meps) - min(meps)) < 1.0,
           f"rango MEP implícito: {min(meps):,.2f}–{max(meps):,.2f}" if meps else "sin datos USD")

    # B12) Q1 con corte pasado <= Q1 hoy (las altas solo se acumulan).
    q1_pasado = {s["segmento"]: s["n"] for s in
                 cs.informe_cuentas_por_segmento(fecha=hasta)["segmentos"]}
    q1_hoy = {s["segmento"]: s["n"] for s in
              cs.informe_cuentas_por_segmento(fecha=date.today().isoformat())["segmentos"]}
    difs12 = [s for s, n in q1_pasado.items() if n > q1_hoy.get(s, 0)]
    _check("B12 Q1 acumulado: cuentas al corte pasado <= cuentas hoy", not difs12,
           ", ".join(difs12[:4]))

    # B13) Q1 ctas_ops por segmento == SQL directo (cuentas distintas que operaron el mes).
    mes_ini_q1 = hasta[:8] + "01"
    base13 = {r["seg"]: float(r["n"]) for r in _q(
        "SELECT COALESCE(c.nivel_1, '(sin segmentar)') AS seg, "
        "COUNT(DISTINCT nm.id_cuenta) AS n "
        "FROM negocio_movimientos nm JOIN comitentes c ON c.id_cuenta = nm.id_cuenta "
        "AND c.estado = 'Activa' WHERE nm.categoria = ANY(%(cats)s) "
        "AND nm.unidad IS DISTINCT FROM 'USDL' AND nm.fecha >= %(m)s AND nm.fecha <= %(h)s "
        "GROUP BY 1", {"cats": list(_CATS_VOLUMEN), "m": mes_ini_q1, "h": hasta})}
    q1c = cs.informe_cuentas_por_segmento(fecha=hasta)
    _cmp_maps("B13 Q1 ctas_ops por segmento == base",
              base13, {s["segmento"]: float(s["ctas_ops"]) for s in q1c["segmentos"]
                       if s["ctas_ops"]})

    # B14) Filtro por operador INEXISTENTE → informe vacío (no explota, no filtra mal).
    d14 = cs.informe_segmento_detalle(segmento="todos", operador="qa_no_existe@x.com",
                                      moneda="ARS", fecha=hasta, desde=desde)
    _check("B14 operador inexistente → detalle vacío",
           d14["n_clientes"] == 0 and not d14["operaciones"])

    # B15) DIVISION + OPERADOR combinados: subconjunto del operador solo.
    divs = [r["division"] for r in _q(
        "SELECT division FROM comitentes WHERE division IS NOT NULL AND estado='Activa' "
        "GROUP BY division ORDER BY count(*) DESC LIMIT 1")]
    if divs:
        con_div = cs.informe_comercial(moneda="ARS", fecha=hasta, desde=desde,
                                       operador=(top_email,), division=(divs[0],))
        solo_op = cs.informe_comercial(moneda="ARS", fecha=hasta, desde=desde,
                                       operador=(top_email,))
        t_con = sum(c["ar_total"] for c in con_div["comerciales"])
        t_solo = sum(c["ar_total"] for c in solo_op["comerciales"])
        _check(f"B15 operador+division({divs[0]}) <= operador solo (AND, subconjunto)",
               t_con <= t_solo + _TOL, f"con={t_con:,.2f} vs solo={t_solo:,.2f}")

    # B16) Cap del payload (max_ops): trunca la lista pero n_operaciones trae el total.
    d16 = cs.informe_segmento_detalle(segmento="todos", moneda="ARS", fecha=hasta,
                                      desde=desde, max_ops=10)
    d16_full = cs.informe_segmento_detalle(segmento="todos", moneda="ARS", fecha=hasta,
                                           desde=desde)
    _check("B16 max_ops=10 → 10 filas + n_operaciones == total real",
           len(d16["operaciones"]) == min(10, d16["n_operaciones"])
           and d16["n_operaciones"] == len(d16_full["operaciones"]),
           f"filas={len(d16['operaciones'])}, n_operaciones={d16['n_operaciones']}, "
           f"real={len(d16_full['operaciones'])}")
    # ...y el cap NO altera los clientes ni sus totales.
    _check("B16 max_ops no altera la tabla de clientes",
           d16["clientes"] == d16_full["clientes"])


# ── BATERÍA 3: rangos genéricos + Control Comercial + Análisis Comercial ──────

def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _informe_ventana(tag: str, titulo: str, desde: str, hasta: str) -> None:
    """Para un [desde, hasta] GENÉRICO: ar_total, vol_total y — clave — que el
    ARANCEL MES tome el MES CALENDARIO del HASTA [1º de ese mes, hasta]."""
    mes_ini = hasta[:8] + "01"
    print(f"  · ventana {titulo}: desde={desde} hasta={hasta} → MES esperado=[{mes_ini}, {hasta}]")
    inf = cs.informe_comercial(moneda="ARS", fecha=hasta, desde=desde)
    rank = {(c["operador_email"] or "").strip().lower() or "(sin operador)": c
            for c in inf["comerciales"]}
    _cmp_maps(f"{tag}a ar_total por operador == base [{desde}, {hasta}]",
              _ar_por_operador(desde, hasta),
              {k: c["ar_total"] for k, c in rank.items() if c["ar_total"]})
    _cmp_maps(f"{tag}b ARANCEL MES toma el mes del HASTA == base [{mes_ini}, {hasta}]",
              _ar_por_operador(mes_ini, hasta),
              {k: c["ar_mes"] for k, c in rank.items() if c["ar_mes"]})
    _cmp_maps(f"{tag}c vol_total por operador == base [{desde}, {hasta}]",
              _vol_por_operador(desde, hasta),
              {k: c["vol_total"] for k, c in rank.items() if c["vol_total"]})


def _bateria3_informe_generico() -> None:
    """G1..G6: seis ventanas DESDE/HASTA de todo tipo (larga, corta, 1 día, trimestre,
    intra-mes, cruce de año). En cada una se valida TOTAL y que MES = mes del HASTA."""
    hoy = _hoy_art()
    mes_ini = hoy.replace(day=1)
    fin_mes_pasado = mes_ini - timedelta(days=1)
    print(f"\n{'=' * 78}\nBATERÍA 3.1 — INFORME con DESDE/HASTA genéricos "
          f"(¿ARANC MES toma el mes del HASTA?)\n{'=' * 78}")

    # G1 — rango LARGO (~9 meses) terminando en el pasado.
    _informe_ventana("G1", "LARGA ~9 meses", _add_months(mes_ini, -9).isoformat(),
                     fin_mes_pasado.isoformat())
    # G2 — rango CORTO (últimos 7 días corridos hasta hoy).
    _informe_ventana("G2", "CORTA 7 días", (hoy - timedelta(days=6)).isoformat(),
                     hoy.isoformat())
    # G3 — UN SOLO DÍA (el último día hábil con operaciones, >= 7 días atrás).
    d1 = _q("SELECT max(concertacion) AS f FROM operaciones WHERE concertacion <= %(h)s",
            {"h": (hoy - timedelta(days=7)).isoformat()})[0]["f"]
    if d1:
        _informe_ventana("G3", "UN SOLO DÍA", d1.isoformat(), d1.isoformat())
    # G4 — TRIMESTRE completo (3 meses cerrados).
    _informe_ventana("G4", "TRIMESTRE", _add_months(mes_ini, -3).isoformat(),
                     (mes_ini - timedelta(days=1)).isoformat())
    # G5 — DESDE y HASTA genéricos A MITAD DE MES (día 10 → día 20 del mes pasado):
    # acá el MES [1º, 20] es MÁS ANCHO que el TOTAL [10, 20] → ar_mes >= ar_total.
    g5_d = fin_mes_pasado.replace(day=10).isoformat()
    g5_h = fin_mes_pasado.replace(day=20).isoformat()
    _informe_ventana("G5", "INTRA-MES día 10→20", g5_d, g5_h)
    inf5 = cs.informe_comercial(moneda="ARS", fecha=g5_h, desde=g5_d)
    malos5 = [c["operador_nombre"] for c in inf5["comerciales"]
              if c["ar_mes"] + _TOL < c["ar_total"]]
    _check("G5d con DESDE a mitad de mes: ar_mes (mes entero) >= ar_total (10→20)",
           not malos5, ", ".join(malos5[:4]))
    # G6 — CRUCE DE AÑO (dic del año pasado → ene de este año).
    _informe_ventana("G6", "CRUCE DE AÑO", date(hoy.year - 1, 12, 1).isoformat(),
                     date(hoy.year, 1, 31).isoformat())


def _cc_base_rango(d0: date, d1: date) -> dict:
    """Recomputo independiente de un período de Control Comercial (sin join, misma
    semántica que la Tabla 1): clientes activos + volumen pesificado + comisiones."""
    pesif = ("CASE WHEN moneda = 'ARS' THEN abs(COALESCE(importe, 0)) "
             "ELSE abs(COALESCE(importe, 0)) * COALESCE(mep, 0) END")
    v = _q(f"SELECT COUNT(DISTINCT id_cuenta) AS n, COALESCE(SUM({pesif}), 0) AS vol "
           f"FROM negocio_movimientos WHERE categoria = ANY(%(cats)s) "
           f"AND unidad IS DISTINCT FROM 'USDL' AND fecha >= %(d)s AND fecha <= %(h)s",
           {"cats": list(_CATS_VOLUMEN), "d": d0, "h": d1})[0]
    c = _q("SELECT COALESCE(SUM(arancel), 0) AS com FROM operaciones "
           "WHERE arancel > 0 AND etapa IS DISTINCT FROM 'solicitud' "
           "AND concertacion >= %(d)s AND concertacion <= %(h)s", {"d": d0, "h": d1})[0]
    return {"clientes_activos": int(v["n"]), "volumen": _f(v["vol"]), "comisiones": _f(c["com"])}


def _bateria3_control() -> None:
    """CC1..CC13: CONTROL COMERCIAL — Tabla 1 (períodos fijos) fila por fila contra la
    base + Tabla 2 (por operador con Desde/Hasta) contra recomputo independiente."""
    print(f"\n{'=' * 78}\nBATERÍA 3.2 — CONTROL COMERCIAL (Tabla 1 períodos fijos + "
          f"Tabla 2 por operador)\n{'=' * 78}")
    t1 = ccs.datos_totales_alyc(moneda="ARS")
    a = date.fromisoformat(t1["ancla"])
    print(f"  ancla (última fecha con operaciones): {a}")
    lun = a - timedelta(days=a.weekday())
    m1, y1 = a.replace(day=1), a.replace(month=1, day=1)
    rangos = {
        "Día": (a, a), "Semana": (lun, a), "Mes": (m1, a), "YTD": (y1, a),
        "12 Meses": (_add_months(a, -12) + timedelta(days=1), a),
        "2025": (date(2025, 1, 1), date(2025, 12, 31)),
        "2024": (date(2024, 1, 1), date(2024, 12, 31)),
        "Total": (date(2000, 1, 1), a),
    }
    for fila in t1["filas"]:
        per = fila["periodo"]
        d0, d1 = rangos[per]
        base = _cc_base_rango(d0, d1)
        difs = [f"{k}: vista={fila[k]:,.2f} vs base={base[k]:,.2f}"
                for k in ("clientes_activos", "volumen", "comisiones")
                if abs(float(fila[k]) - base[k]) > _TOL]
        _check(f"CC Tabla1 '{per}' [{d0}→{d1}]: activos/volumen/comisiones == base",
               not difs, "; ".join(difs))

    # CC9) El % de variación de la fila "Mes" == recomputo vs el mes anterior completo.
    fmes = next(f for f in t1["filas"] if f["periodo"] == "Mes")
    prev = _cc_base_rango(_add_months(m1, -1), m1 - timedelta(days=1))
    esperado = (round((fmes["volumen"] - prev["volumen"]) / prev["volumen"] * 100, 1)
                if prev["volumen"] else None)
    ok9 = (fmes["volumen_pct"] is None and esperado is None) or \
          (fmes["volumen_pct"] is not None and esperado is not None
           and abs(fmes["volumen_pct"] - esperado) <= 0.2)
    _check("CC9 Tabla1 'Mes': % volumen == variación vs mes anterior completo",
           ok9, f"vista={fmes['volumen_pct']} vs esperado={esperado}")

    # Tabla 2 — por operador en el MES PASADO COMPLETO (rango determinístico).
    hoy = _hoy_art()
    d1t = hoy.replace(day=1) - timedelta(days=1)
    d0t = d1t.replace(day=1)
    t2 = ccs.datos_por_operador(desde=d0t.isoformat(), hasta=d1t.isoformat(), moneda="ARS")
    filas2 = {f["operador_email"]: f for f in t2["filas"]}
    op_de = {r["id_cuenta"]: r["operador_email"] for r in _q(
        "SELECT id_cuenta, operador_email FROM comitentes "
        "WHERE estado = 'Activa' AND operador_email IS NOT NULL")}

    def _t2_base(bd0: date, bd1: date) -> dict[str, dict]:
        pesif = ("CASE WHEN moneda = 'ARS' THEN abs(COALESCE(importe, 0)) "
                 "ELSE abs(COALESCE(importe, 0)) * COALESCE(mep, 0) END")
        out: dict[str, dict] = {}
        for r in _q(f"SELECT id_cuenta, SUM({pesif}) AS vol FROM negocio_movimientos "
                    f"WHERE categoria = ANY(%(cats)s) AND unidad IS DISTINCT FROM 'USDL' "
                    f"AND fecha >= %(d)s AND fecha <= %(h)s GROUP BY id_cuenta",
                    {"cats": list(_CATS_VOLUMEN), "d": bd0, "h": bd1}):
            op = op_de.get(r["id_cuenta"])
            if op:
                s = out.setdefault(op, {"activos": 0, "vol": 0.0, "com": 0.0})
                s["activos"] += 1
                s["vol"] += _f(r["vol"])
        for r in _q("SELECT id_cuenta, SUM(arancel) AS com FROM operaciones "
                    "WHERE arancel > 0 AND etapa IS DISTINCT FROM 'solicitud' "
                    "AND concertacion >= %(d)s AND concertacion <= %(h)s GROUP BY id_cuenta",
                    {"d": bd0, "h": bd1}):
            op = op_de.get(r["id_cuenta"])
            if op:
                out.setdefault(op, {"activos": 0, "vol": 0.0, "com": 0.0})["com"] += _f(r["com"])
        return out

    base2 = _t2_base(d0t, d1t)
    print(f"  Tabla 2: rango {d0t} → {d1t}, {len(filas2)} operadores")
    _cmp_maps("CC10 Tabla2 comisiones por operador == base",
              {k: v["com"] for k, v in base2.items() if v["com"]},
              {k: f["comisiones"] for k, f in filas2.items() if f["comisiones"]})
    _cmp_maps("CC11 Tabla2 volumen por operador == base",
              {k: v["vol"] for k, v in base2.items() if v["vol"]},
              {k: f["volumen"] for k, f in filas2.items() if f["volumen"]})
    _cmp_maps("CC12 Tabla2 clientes ACTIVOS por operador == base (operó >=1 vez)",
              {k: float(v["activos"]) for k, v in base2.items() if v["activos"]},
              {k: float(f["clientes_activos"]) for k, f in filas2.items()
               if f["clientes_activos"]})
    # CC13) activos + inactivos == total de clientes del comercial (comitentes Activa).
    tot_cli = {r["operador_email"]: int(r["n"]) for r in _q(
        "SELECT operador_email, count(*) AS n FROM comitentes WHERE estado = 'Activa' "
        "AND operador_email IS NOT NULL GROUP BY operador_email")}
    difs13 = [op for op, f in filas2.items()
              if f["clientes_activos"] + f["clientes_inactivos"] != tot_cli.get(op, 0)
              and f["clientes_activos"] <= tot_cli.get(op, 0)]
    _check("CC13 Tabla2 activos + inactivos == total clientes del comercial",
           not difs13, f"{len(difs13)} operadores no cierran: {difs13[:4]}")
    # CC14) % de comisiones del operador top == variación vs rango anterior de igual largo.
    if filas2:
        top_op = max(filas2, key=lambda o: filas2[o]["volumen"])
        dias = (d1t - d0t).days
        p1 = d0t - timedelta(days=1)
        prev2 = _t2_base(p1 - timedelta(days=dias), p1)
        pcom = prev2.get(top_op, {}).get("com", 0.0)
        esp = round((filas2[top_op]["comisiones"] - pcom) / pcom * 100, 1) if pcom else None
        vis = filas2[top_op]["comisiones_pct"]
        ok14 = (vis is None and esp is None) or \
               (vis is not None and esp is not None and abs(vis - esp) <= 0.2)
        _check(f"CC14 Tabla2 % comisiones de {top_op!r} == vs rango anterior igual largo",
               ok14, f"vista={vis} vs esperado={esp}")


def _bateria3_analisis() -> None:
    """AC1..AC9: ANÁLISIS COMERCIAL — universo, estados ACTIVA/ENFRIANDOSE/DORMIDA/NUEVA,
    última operación contra la base, flags MTD/YTD y foto a una fecha pasada."""
    print(f"\n{'=' * 78}\nBATERÍA 3.3 — ANÁLISIS COMERCIAL (estados / última op / "
          f"MTD-YTD / foto al pasado)\n{'=' * 78}")
    hoy = _hoy_art()
    res = cs.analisis_comercial(operador="__todos__", moneda="ARS")
    clientes = res["clientes"]

    # AC1) Universo completo == comitentes Activa.
    n_base = int(_q("SELECT count(*) AS n FROM comitentes WHERE estado = 'Activa' "
                    "AND id_cuenta IS NOT NULL")[0]["n"])
    _check("AC1 universo (TODOS) == comitentes con estado Activa",
           len(clientes) == n_base, f"vista={len(clientes)} vs base={n_base}")

    # AC2) Estado coherente con dias_sin_operar (regla 45/90) en TODOS los clientes.
    def _esperado(c: dict) -> str:
        if not c["ultima_op"]:
            return "NUEVA"
        d = c["dias_sin_operar"]
        return "ACTIVA" if d <= 45 else ("ENFRIANDOSE" if d <= 90 else "DORMIDA")
    mal2 = [c["id_cuenta"] for c in clientes if c["estado"] != _esperado(c)]
    _check("AC2 estado == regla ACTIVA<=45 / ENFRIANDOSE<=90 / DORMIDA / NUEVA (todos)",
           not mal2, f"{len(mal2)} cuentas mal clasificadas: {mal2[:4]}")

    # AC3) ultima_op == max(concertacion) real en la base (muestra de ~20 cuentas).
    paso = max(1, len(clientes) // 20)
    muestra = clientes[::paso][:20]
    ults = {r["id_cuenta"]: r["u"] for r in _q(
        "SELECT id_cuenta, max(concertacion) AS u FROM operaciones "
        "WHERE id_cuenta = ANY(%(i)s) GROUP BY id_cuenta",
        {"i": [c["id_cuenta"] for c in muestra]})}
    mal3 = [c["id_cuenta"] for c in muestra
            if (c["ultima_op"] or None) != (ults[c["id_cuenta"]].isoformat()
                                            if c["id_cuenta"] in ults else None)]
    _check(f"AC3 última operación == base (muestra de {len(muestra)} cuentas)",
           not mal3, f"difieren: {mal3[:4]}")

    # AC4) dias_sin_operar == hoy - ultima_op (consistencia interna del payload).
    mal4 = [c["id_cuenta"] for c in clientes if c["ultima_op"]
            and c["dias_sin_operar"] != (hoy - date.fromisoformat(c["ultima_op"])).days]
    _check("AC4 dias_sin_operar == días corridos desde la última op", not mal4,
           f"{len(mal4)} cuentas: {mal4[:4]}")

    # AC5/AC6) Flags MTD / YTD contra la última op.
    m1, y1 = hoy.replace(day=1).isoformat(), date(hoy.year, 1, 1).isoformat()
    mal5 = [c["id_cuenta"] for c in clientes
            if c["opero_mtd"] != (bool(c["ultima_op"]) and c["ultima_op"] >= m1)]
    _check("AC5 opero_mtd == (última op en el mes actual)", not mal5,
           f"{len(mal5)} cuentas: {mal5[:4]}")
    mal6 = [c["id_cuenta"] for c in clientes
            if c["opero_ytd"] != (bool(c["ultima_op"]) and c["ultima_op"] >= y1)]
    _check("AC6 opero_ytd == (última op en el año)", not mal6,
           f"{len(mal6)} cuentas: {mal6[:4]}")

    # AC7) Conteo por estado == recomputo TOTAL desde la base (todas las cuentas Activa).
    base_ult = {r["id_cuenta"]: r["u"] for r in _q(
        "SELECT o.id_cuenta, max(o.concertacion) AS u FROM operaciones o "
        "JOIN comitentes c ON c.id_cuenta = o.id_cuenta AND c.estado = 'Activa' "
        "GROUP BY o.id_cuenta")}
    ids_all = [r["id_cuenta"] for r in _q(
        "SELECT id_cuenta FROM comitentes WHERE estado = 'Activa' AND id_cuenta IS NOT NULL")]
    cnt_base: dict[str, int] = {}
    for idc in ids_all:
        u = base_ult.get(idc)
        d = (hoy - u).days if u else None
        est = ("NUEVA" if d is None else
               "ACTIVA" if d <= 45 else "ENFRIANDOSE" if d <= 90 else "DORMIDA")
        cnt_base[est] = cnt_base.get(est, 0) + 1
    cnt_vista: dict[str, int] = {}
    for c in clientes:
        cnt_vista[c["estado"]] = cnt_vista.get(c["estado"], 0) + 1
    difs7 = [f"{e}: vista={cnt_vista.get(e, 0)} vs base={cnt_base.get(e, 0)}"
             for e in ("ACTIVA", "ENFRIANDOSE", "DORMIDA", "NUEVA")
             if cnt_vista.get(e, 0) != cnt_base.get(e, 0)]
    resumen = " · ".join(f"{e}={cnt_vista.get(e, 0)}"
                         for e in ("ACTIVA", "ENFRIANDOSE", "DORMIDA", "NUEVA"))
    _check("AC7 conteo por estado (operativas/inactivas) == base", not difs7,
           "; ".join(difs7) if difs7 else resumen)

    # AC8) FOTO al pasado (fin del mes pasado): nada posterior al corte + altas <= corte.
    corte = (hoy.replace(day=1) - timedelta(days=1)).isoformat()
    foto = cs.analisis_comercial(operador="__todos__", moneda="ARS", fecha=corte)
    tarde = [c["id_cuenta"] for c in foto["clientes"]
             if c["ultima_op"] and c["ultima_op"] > corte]
    _check(f"AC8 foto al {corte}: ninguna última op posterior al corte", not tarde,
           f"{len(tarde)} cuentas: {tarde[:4]}")
    ids_foto = [c["id_cuenta"] for c in foto["clientes"]]
    altas_mal = _q("SELECT id_cuenta FROM comitentes WHERE id_cuenta = ANY(%(i)s) "
                   "AND fecha_alta_legajo > %(c)s", {"i": ids_foto, "c": corte}) \
        if ids_foto else []
    _check(f"AC8b foto al {corte}: sin cuentas dadas de alta después del corte",
           not altas_mal, f"{len(altas_mal)} cuentas posteriores")

    # AC9) Con `desde` (período custom): opero_mtd pasa a ser "operó en [desde, hoy]".
    desde9 = (hoy - timedelta(days=10)).isoformat()
    res9 = cs.analisis_comercial(operador="__todos__", moneda="ARS", desde=desde9)
    mal9 = [c["id_cuenta"] for c in res9["clientes"]
            if c["opero_mtd"] != (bool(c["ultima_op"]) and c["ultima_op"] >= desde9)]
    _check(f"AC9 con desde={desde9}: opero_mtd == operó en el período", not mal9,
           f"{len(mal9)} cuentas: {mal9[:4]}")


def main() -> None:
    hoy = date.today()
    mes_ini = hoy.replace(day=1)
    fin_mes_pasado = mes_ini - timedelta(days=1)
    ini_mes_pasado = fin_mes_pasado.replace(day=1)

    # (a) histórico hasta hoy (sin desde/hasta = default de la vista).
    _escenario("HISTÓRICO (sin filtros de fecha)", None, None)
    # (b) mes actual [1º, hoy] — el uso más común del Desde/Hasta.
    _escenario("MES ACTUAL", mes_ini.isoformat(), hoy.isoformat())
    # (c) mes pasado completo — valida que el corte en el pasado NO arrastre ops nuevas.
    _escenario("MES PASADO COMPLETO", ini_mes_pasado.isoformat(), fin_mes_pasado.isoformat())

    # (d) partición por división (mes actual).
    _particion_division(mes_ini.isoformat(), hoy.isoformat())

    # (e) batería 2: drill-downs, cuenta individual, moneda, cortes intra-mes, cap.
    _bateria2(mes_ini.isoformat(), hoy.isoformat())

    # (f) batería 3: ventanas genéricas + Control Comercial + Análisis Comercial.
    _bateria3_informe_generico()
    _bateria3_control()
    _bateria3_analisis()

    print(f"\n{'=' * 78}")
    if _FAILS:
        print(f"RESULTADO: {len(_FAILS)} CHECK(S) FALLARON:")
        for f in _FAILS:
            print(f"  ✗ {f}")
        sys.exit(1)
    print("RESULTADO: TODOS LOS CHECKS PASARON ✓ — el Informe coincide con la base.")


if __name__ == "__main__":
    main()
