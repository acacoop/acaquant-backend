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

Uso (Droplet): python -m scripts.qa_informe
Read-only (solo SELECT). Exit code 1 si algún check falla.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

from api.services import comercial_sql as cs
from api.services._sql import _f, _q
from api.services.comercial import _CATS_VOLUMEN

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

    print(f"\n{'=' * 78}")
    if _FAILS:
        print(f"RESULTADO: {len(_FAILS)} CHECK(S) FALLARON:")
        for f in _FAILS:
            print(f"  ✗ {f}")
        sys.exit(1)
    print("RESULTADO: TODOS LOS CHECKS PASARON ✓ — el Informe coincide con la base.")


if __name__ == "__main__":
    main()
