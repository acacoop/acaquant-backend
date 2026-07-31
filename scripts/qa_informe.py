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

    print(f"\n{'=' * 78}")
    if _FAILS:
        print(f"RESULTADO: {len(_FAILS)} CHECK(S) FALLARON:")
        for f in _FAILS:
            print(f"  ✗ {f}")
        sys.exit(1)
    print("RESULTADO: TODOS LOS CHECKS PASARON ✓ — el Informe coincide con la base.")


if __name__ == "__main__":
    main()
