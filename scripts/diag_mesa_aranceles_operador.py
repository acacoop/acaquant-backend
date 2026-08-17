"""diag_mesa_aranceles_operador — ¿se puede sumar el 50% de Mesa de Dinero al
arancel del operador, y cuánto mueve la aguja? READ-ONLY.

POR QUÉ EXISTE (REGLA #2). El pedido es que el resultado que un operador genera
en Mesa de Dinero (su 50% de la regla de reparto) aparezca también en "los
aranceles del operador" — el ranking de comerciales del Informe y la tabla
ARANCELES por OPERADOR. Las dos mitades del cruce viven en universos distintos
y NO comparten clave:

    ARANCEL   → operaciones.operaciones.arancel, atribuido por `id_cuenta`
                → clientes.comitentes.operador_email → clientes.operadores
    MESA      → operaciones.mesa_dinero.resultado, atribuido por el TEXTO de
                `observacion`, que el service valida contra el NOMBRE del
                operador (clientes.operadores.nombre), no contra su email

O sea que el único puente disponible es el NOMBRE. Antes de escribir una línea
de código que sume las dos cosas hay que saber tres números que no se pueden
suponer: (1) si cada `observacion` resuelve a UN operador del catálogo (un
nombre repetido o una observación huérfana rompe la atribución en silencio),
(2) cuánta plata hay de cada lado (si la Mesa es el 0,3% del arancel el
esfuerzo no se justifica; si es el 40% cambia el ranking), y (3) cuántos días
del período no tienen TC cargado, porque esos no suman USD y la columna en
dólares quedaría corta sin avisar.

QUÉ NO HACE: no escribe nada, no toca la vista. Solo mide y reporta.

Uso (Droplet, raíz):
    python -m scripts.diag_mesa_aranceles_operador
    python -m scripts.diag_mesa_aranceles_operador --desde 2026-01-01 --hasta 2026-08-17
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from api.services._sql import _f, _q

OBSERVACION_MESA = "Mesa"


def _money(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:>16,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _linea(n: int = 92) -> None:
    print("─" * n)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--desde", help="ISO. Default: hace 12 meses")
    ap.add_argument("--hasta", help="ISO. Default: hoy")
    args = ap.parse_args()

    hoy = date.today()
    hasta = args.hasta or hoy.isoformat()
    desde = args.desde or (hoy - timedelta(days=365)).isoformat()
    p = {"desde": desde, "hasta": hasta}

    print("=" * 92)
    print(f"MESA DE DINERO vs ARANCELES POR OPERADOR — período [{desde} … {hasta}]")
    print("=" * 92)

    # ── 1. Catálogo de operadores: ¿el NOMBRE alcanza como clave? ────────────
    ops_cat = _q("SELECT email, nombre FROM operadores ORDER BY nombre")
    por_nombre: dict[str, list[str]] = {}
    for r in ops_cat:
        nom = (r["nombre"] or "").strip()
        if nom:
            por_nombre.setdefault(nom, []).append(r["email"])
    ambiguos = {n: e for n, e in por_nombre.items() if len(e) > 1}
    print(f"\n1) CATÁLOGO clientes.operadores: {len(ops_cat)} operadores, "
          f"{len(por_nombre)} nombres distintos")
    if ambiguos:
        print("   ⚠ NOMBRES REPETIDOS (el join por nombre sería ambiguo):")
        for n, emails in ambiguos.items():
            print(f"     - {n!r} → {emails}")
    else:
        print("   ✓ sin nombres repetidos → el join por nombre es unívoco")
    sin_nombre = [r["email"] for r in ops_cat if not (r["nombre"] or "").strip()]
    if sin_nombre:
        print(f"   ⚠ {len(sin_nombre)} operadores SIN nombre cargado: {sin_nombre[:5]}")

    # ── 2. Mesa de Dinero por observación ────────────────────────────────────
    cobertura = _q("SELECT min(fecha) AS f0, max(fecha) AS f1, count(*) AS n "
                   "FROM mesa_dinero")[0]
    print(f"\n2) operaciones.mesa_dinero — {cobertura['n']} ops en total, "
          f"desde {cobertura['f0']} hasta {cobertura['f1']}")

    mesa = _q(
        "SELECT COALESCE(NULLIF(btrim(observacion), ''), '(sin observación)') AS obs, "
        "       count(*) AS n, SUM(resultado) AS ars "
        "FROM mesa_dinero WHERE fecha >= %(desde)s AND fecha <= %(hasta)s "
        "GROUP BY 1 ORDER BY 3 DESC NULLS LAST", p)
    if not mesa:
        print("   (sin operaciones de mesa en el período)")
        return

    print(f"\n   {'OBSERVACIÓN':<28}{'N':>5}{'RESULTADO ARS':>19}{'50% OPERADOR':>19}  MATCH")
    _linea()
    total_mesa = 0.0
    total_mitad = 0.0
    huerfanas: list[str] = []
    mitad_por_email: dict[str, float] = {}
    for r in mesa:
        obs = r["obs"]
        ars = _f(r["ars"]) or 0.0
        total_mesa += ars
        es_mesa = obs == OBSERVACION_MESA
        sin_obs = obs == "(sin observación)"
        emails = por_nombre.get(obs, [])
        if es_mesa or sin_obs:
            match = "— (no se reparte)"
            mitad = 0.0
        elif len(emails) == 1:
            match = f"✓ {emails[0]}"
            mitad = ars / 2
            mitad_por_email[emails[0]] = mitad_por_email.get(emails[0], 0.0) + mitad
        elif len(emails) > 1:
            match = f"⚠ AMBIGUO ({len(emails)} emails)"
            mitad = ars / 2
        else:
            match = "✗ NO está en el catálogo"
            mitad = ars / 2
            huerfanas.append(obs)
        total_mitad += mitad
        print(f"   {obs[:27]:<28}{r['n']:>5}{_money(ars)}{_money(mitad) if mitad else '                —':>19}"
              f"  {match}")
    _linea()
    print(f"   {'TOTAL':<28}{sum(x['n'] for x in mesa):>5}{_money(total_mesa)}{_money(total_mitad)}")
    if huerfanas:
        print(f"\n   ⚠ {len(huerfanas)} observación(es) que NO resuelven a un operador "
              f"del catálogo: {huerfanas}")
        print("     (esa plata NO se podría atribuir a nadie en el ranking de comerciales)")

    # ── 3. TC: cuántos días del período no tienen tipo de cambio cargado ─────
    tc = _q(
        "SELECT count(DISTINCT o.fecha) AS dias, "
        "       count(DISTINCT o.fecha) FILTER (WHERE t.tc IS NULL) AS sin_tc "
        "FROM mesa_dinero o LEFT JOIN mesa_dinero_tc t ON t.fecha = o.fecha "
        "WHERE o.fecha >= %(desde)s AND o.fecha <= %(hasta)s", p)[0]
    print(f"\n3) TC MANUAL: {tc['sin_tc']} de {tc['dias']} días con ops NO tienen TC "
          f"cargado en mesa_dinero_tc")
    if tc["sin_tc"]:
        print("   ⚠ el resultado de esos días no suma en USD → una columna en dólares "
              "quedaría corta.")

    # ── 4. Arancel del mismo período, por operador (predicado de la vista) ───
    # Mismo WHERE que _rollup_por_cuenta / ops_aranceles: arancel > 0, etapa
    # distinta de 'solicitud', no anulado. Se agrupa por el operador de la cuenta.
    ar = _q(
        "SELECT c.operador_email AS email, "
        "       COALESCE(o.nombre, o.email, '(sin operador)') AS nombre, "
        "       SUM(op.arancel) AS ar "
        "FROM operaciones op "
        "JOIN comitentes c ON c.id_cuenta = op.id_cuenta AND c.estado = 'Activa' "
        "LEFT JOIN operadores o ON o.email = c.operador_email "
        "WHERE op.arancel > 0 AND op.etapa IS DISTINCT FROM 'solicitud' "
        "  AND op.anulado_en IS NULL "
        "  AND op.concertacion >= %(desde)s AND op.concertacion <= %(hasta)s "
        "GROUP BY 1, 2 ORDER BY 3 DESC", p)
    ar_por_email = {r["email"]: _f(r["ar"]) or 0.0 for r in ar if r["email"]}
    nombre_de = {r["email"]: r["nombre"] for r in ar if r["email"]}
    total_ar = sum(ar_por_email.values())

    print(f"\n4) ARANCEL del período (operaciones.operaciones) = {_money(total_ar).strip()} ARS")
    print(f"\n   {'OPERADOR':<28}{'ARANCEL':>19}{'MESA 50%':>19}{'MESA / TOTAL':>15}")
    _linea()
    emails = set(ar_por_email) | set(mitad_por_email)
    filas = []
    for e in emails:
        a = ar_por_email.get(e, 0.0)
        m = mitad_por_email.get(e, 0.0)
        filas.append((a + m, e, a, m))
    for _, e, a, m in sorted(filas, reverse=True):
        nom = nombre_de.get(e) or por_nombre_inv(por_nombre, e) or e
        pct = (m / (a + m) * 100) if (a + m) else 0.0
        print(f"   {nom[:27]:<28}{_money(a)}{_money(m) if m else '                —':>19}"
              f"{pct:>14.1f}%")
    _linea()
    pct_tot = (total_mitad / (total_ar + total_mitad) * 100) if (total_ar + total_mitad) else 0.0
    print(f"   {'TOTAL':<28}{_money(total_ar)}{_money(total_mitad)}{pct_tot:>14.1f}%")

    print("\n" + "=" * 92)
    print("LECTURA: la columna 'MESA / TOTAL' dice cuánto del 'generado' de cada")
    print("operador vendría de Mesa de Dinero si las dos fuentes se suman. Si hay")
    print("observaciones huérfanas o nombres ambiguos, eso se arregla PRIMERO (en el")
    print("catálogo de operadores) — si no, el ranking sumaría plata que no se puede")
    print("atribuir, o se la daría al operador equivocado.")
    print("=" * 92)


def por_nombre_inv(por_nombre: dict[str, list[str]], email: str) -> str | None:
    for nom, emails in por_nombre.items():
        if email in emails:
            return nom
    return None


if __name__ == "__main__":
    main()
