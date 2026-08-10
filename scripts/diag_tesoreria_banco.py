"""Diag read-only: por qué UN banco sale en la grilla BANCOS y no en el catálogo.

La grilla BANCOS no se arma solo con el catálogo: se arma con la UNIÓN de
  (bancos ACTIVOS del catálogo) ∪ (todo banco que ese día aparezca en alguna fuente)
—movimientos de Aunesa, cheques, mercados/FCI, registros manuales, banco a banco—.
El ABM (botón «BANCOS»), en cambio, lista SOLO las filas con `activa = true`.

Entre esas dos reglas quedan tres huecos posibles, y este script dice cuál es:

  A. El banco está en `tesoreria_cuentas` con `activa = false` (BAJA LÓGICA). Sigue
     operando → entra a la grilla por la unión, pero el ABM lo esconde. `registrar_cuentas`
     (el auto-alta del poll) NO revive la fila: su UPDATE no toca `activa`.
  B. El banco NO está en `tesoreria_cuentas` (el auto-alta nunca corrió o falló: se
     loguea warning y sigue, la vista no se entera).
  C. SÍ está, pero con el nombre escrito distinto (mayúsculas/espacios). Aunesa manda
     la denominación TAL CUAL (solo `.strip()`), mientras el alta manual pasa a
     MAYÚSCULAS y colapsa espacios → dos filas que se ven casi iguales.

Es 100% READ-ONLY: no escribe nada (a diferencia de la vista, que da de alta cuentas
en cada carga). Una sola llamada a Aunesa (el día pedido).

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_tesoreria_banco --banco COMAFI
    python -m scripts.diag_tesoreria_banco --banco "COMAFI TERCEROS" --fecha 2026-08-08
    python -m scripts.diag_tesoreria_banco --banco COMAFI --montos
"""
from __future__ import annotations

import argparse
from collections import Counter

from api.services._sql import _q
from api.services.tesoreria import (
    ESTADO_EFECTIVO,
    TODOS_ESTADOS,
    _cuenta_operativa,
    _dia,
    _fechas,
    _num,
    aplanar,
    traer_crudas,
)

# Tablas que meten un banco en la grilla aunque no esté en el catálogo. `col` es la
# columna que guarda la DENOMINACIÓN del banco; `fecha` es None cuando la fuente no
# filtra por día (los cheques emitidos y los VEP son tableros de seguimiento).
FUENTES = (
    ("cheques",      "operaciones.tesoreria_cheques",       "banco",       None),
    ("mercados/fci", "operaciones.tesoreria_mercados",      "banco",       "fecha"),
    ("registros",    "operaciones.tesoreria_registros",     "banco",       "fecha"),
    ("veps",         "operaciones.tesoreria_veps",          "banco",       None),
    ("bb débito",    "operaciones.tesoreria_banco_a_banco", "cta_debito",  "fecha"),
    ("bb crédito",   "operaciones.tesoreria_banco_a_banco", "cta_credito", "fecha"),
    ("saldos",       "operaciones.tesoreria_saldos",        "cuenta_operativa", "fecha"),
)


def _rep(s: str) -> str:
    """Nombre con los espacios visibles: `A  B` (doble espacio) vs `A B` se distinguen."""
    return repr(s)


def _catalogo(pat: str) -> list[dict]:
    return _q(
        "SELECT cuenta_operativa, unidad, aunesa_id, activa, primera_vez, ultima_vez, "
        "       numero_cuenta, numero_hygirus "
        "FROM operaciones.tesoreria_cuentas "
        "WHERE cuenta_operativa ILIKE %(p)s ORDER BY cuenta_operativa, unidad",
        {"p": f"%{pat}%"},
    )


def _de_aunesa(dia, pat: str, montos: bool) -> dict[tuple[str, str], dict]:
    """{(denominación, unidad): {estados: Counter, n, monto}} para el día."""
    _, _, yyyymmdd = _fechas(dia.isoformat())
    crudas = traer_crudas(dia=dia, estado=TODOS_ESTADOS)
    out: dict[tuple[str, str], dict] = {}
    for r in crudas:
        cta = _cuenta_operativa(r.get("cuentaOperativa"))
        if pat.lower() not in cta.lower():
            continue
        unidad = (r.get("unidad") or "?").upper()
        mov = aplanar(r, yyyymmdd)
        d = out.setdefault((cta, unidad), {"estados": Counter(), "n": 0, "monto": 0.0,
                                           "id": None, "sin_hora": 0})
        co = r.get("cuentaOperativa")
        if isinstance(co, dict) and co.get("id"):
            d["id"] = str(co["id"])
        d["estados"][str(r.get("estado") or "?").strip()] += 1
        d["n"] += 1
        if not mov["_hora"]:
            d["sin_hora"] += 1
        if montos and str(r.get("estado") or "").strip() == ESTADO_EFECTIVO:
            d["monto"] += _num(r.get("monto"))
    return out


def _otras_fuentes(dia, pat: str) -> list[tuple[str, str, str, int]]:
    """[(fuente, banco, unidad, n)] — carga manual que también mete el banco a la grilla."""
    filas: list[tuple[str, str, str, int]] = []
    for nombre, tabla, col, col_fecha in FUENTES:
        where = f"{col} ILIKE %(p)s"
        params: dict = {"p": f"%{pat}%"}
        if col_fecha:
            where += f" AND {col_fecha} = %(d)s"
            params["d"] = dia
        try:
            rows = _q(f"SELECT {col} AS banco, unidad, COUNT(*) AS n FROM {tabla} "
                      f"WHERE {where} GROUP BY 1, 2 ORDER BY 1, 2", params)
        except Exception as e:  # tabla que todavía no existe en esta DB
            filas.append((nombre, f"<error: {type(e).__name__}>", "", 0))
            continue
        filas += [(nombre, r["banco"], r["unidad"], int(r["n"])) for r in rows]
    return filas


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--banco", required=True,
                    help="parte del nombre, sin importar mayúsculas (ej. COMAFI)")
    ap.add_argument("--fecha", default=None, help="YYYY-MM-DD (default hoy ART)")
    ap.add_argument("--montos", action="store_true",
                    help="además de contar filas, sumar importes (Procesado)")
    args = ap.parse_args()

    dia = _dia(args.fecha)
    pat = args.banco.strip()
    print(f"\n=== TESORERÍA · banco ~ '{pat}' · día {dia.isoformat()} ===\n")

    # 1) Catálogo -------------------------------------------------------------
    cat = _catalogo(pat)
    print(f"-- CATÁLOGO operaciones.tesoreria_cuentas ({len(cat)} fila/s) --")
    if not cat:
        print("   (ninguna fila matchea → el banco NO está en el catálogo)")
    for r in cat:
        estado = "ACTIVA" if r["activa"] else "DADA DE BAJA (activa=false)"
        origen = "descubierta por Aunesa" if r["aunesa_id"] else "alta manual"
        print(f"   {_rep(r['cuenta_operativa']):<45} [{r['unidad']}]  {estado}")
        print(f"      {origen} · aunesa_id={r['aunesa_id']} · "
              f"nro_cuenta={r['numero_cuenta']} · hygirus={r['numero_hygirus']}")
        print(f"      primera_vez={r['primera_vez']} · ultima_vez={r['ultima_vez']}")
    activas = {(r["cuenta_operativa"], r["unidad"]) for r in cat if r["activa"]}
    bajas = {(r["cuenta_operativa"], r["unidad"]) for r in cat if not r["activa"]}

    # 2) Lo que trae Aunesa hoy ----------------------------------------------
    try:
        au = _de_aunesa(dia, pat, args.montos)
        au_ok = True
    except Exception as e:
        au, au_ok = {}, False
        print(f"\n-- AUNESA: no pude consultar ({type(e).__name__}: {e}) --")
    if au_ok:
        print(f"\n-- MOVIMIENTOS DE AUNESA del día ({len(au)} banco/s × moneda) --")
        if not au:
            print("   (ninguno: el banco no operó ese día)")
        for (cta, uni), d in sorted(au.items()):
            extra = f" · Σ Procesado={d['monto']:,.2f}" if args.montos else ""
            print(f"   {_rep(cta):<45} [{uni}]  n={d['n']} · id={d['id']}{extra}")
            print(f"      estados: {dict(d['estados'])} · sin hora (destildados "
                  f"por default): {d['sin_hora']}")

    # 3) Carga manual ---------------------------------------------------------
    otras = _otras_fuentes(dia, pat)
    print(f"\n-- OTRAS FUENTES que lo meten a la grilla ({len(otras)} grupo/s) --")
    if not otras:
        print("   (ninguna)")
    for fuente, banco, uni, n in otras:
        print(f"   {fuente:<14} {_rep(banco):<45} [{uni}]  n={n}")

    # 4) Veredicto ------------------------------------------------------------
    print("\n-- VEREDICTO --")
    en_grilla = set(au) | {(b, u) for _, b, u, n in otras if n}
    if not en_grilla and not activas:
        print("   No aparece en ninguna fuente del día ni en el catálogo activo: "
              "revisá el nombre buscado (--banco acepta una parte).")
    for clave in sorted(en_grilla):
        cta, uni = clave
        if clave in activas:
            print(f"   {_rep(cta)} [{uni}]: OK — está en el catálogo y ACTIVA. "
                  "Si no lo ves en el ABM, mirá si el nombre de la grilla es este mismo.")
        elif clave in bajas:
            print(f"   {_rep(cta)} [{uni}]: CAUSA A — está en el catálogo pero DADO DE "
                  "BAJA. La grilla lo muestra porque sigue teniendo movimientos; el ABM "
                  "solo lista los activos. Para que vuelva al ABM, darlo de alta con el "
                  "MISMO nombre exacto (el alta revive la fila, no duplica).")
        else:
            parecidos = [r for r in cat if r["unidad"] == uni]
            if parecidos:
                print(f"   {_rep(cta)} [{uni}]: CAUSA C — no hay fila con ESE nombre "
                      f"exacto; el catálogo tiene "
                      f"{', '.join(_rep(r['cuenta_operativa']) for r in parecidos)}. "
                      "Comparar mayúsculas y espacios dobles.")
            else:
                print(f"   {_rep(cta)} [{uni}]: CAUSA B — no está en `tesoreria_cuentas`. "
                      "El auto-alta del poll no lo registró (falla silenciosa) o es la "
                      "primera vez que opera y todavía no se cargó la vista.")
    print()


if __name__ == "__main__":
    main()
