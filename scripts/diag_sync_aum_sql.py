"""scripts/diag_sync_aum_sql.py — MEDICIÓN read-only antes del sync Mongo ← SQL.

Mide el alcance de corregir `Valuaciones.AuM` (Mongo, lo que ve la web) con
`portafolio.tenencia` (SQL, dato con fecha corregida del backfill). NO escribe nada.

Compara por fecha y por (id_cuenta, unidad), aplicando `_aum_filters` a las filas
del SQL para que el cruce sea manzana-con-manzana (el SQL es SIN exclusiones; el
AuM las aplica). Responde las preguntas que definen la estrategia del sync:

  * ¿Qué fechas están en ambos / solo en Mongo / solo en SQL?
  * Por fecha intersección: cuántos (cuenta,unidad) matchean, cuántos difieren en
    CANTIDAD / PRECIO / VALUACIÓN (= la corrupción real a corregir).
  * only_mongo  → docs que el sync BORRARÍA (están en Mongo, no en el SQL elegible).
  * only_sql    → docs que el sync INSERTARÍA (en SQL, no en Mongo) — estos NO
    tienen tipoTitulo en el SQL → hay que decidir qué hacer con ellos.

Uso:
    python -m scripts.diag_sync_aum_sql                  # todas las fechas que cruzan
    python -m scripts.diag_sync_aum_sql --fecha 2026-05-29   # detalle de una fecha
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client_read
from core.postgres import get_pool
from jobs._aum_filters import (
    is_excluded,
    load_contrapartes_id_cuentas,
    load_contrapartes_names,
)


def _opt(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _fechas_mongo(aum_col) -> set[str]:
    return {str(f) for f in aum_col.distinct("fecha_snapshot") if f}


def _fechas_sql() -> set[str]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT fecha::text FROM portafolio.tenencia")
        return {r[0] for r in cur.fetchall()}


def _mongo_dia(aum_col, fecha: str) -> dict[tuple, tuple]:
    """{(id_cuenta, unidad): (cantidad, precio, valuacion)} del AuM para esa fecha."""
    out: dict[tuple, tuple] = {}
    for d in aum_col.find(
            {"fecha_snapshot": fecha},
            {"_id": 0, "id_cuenta": 1, "unidad": 1, "cantidad": 1,
             "precio": 1, "valuacion": 1}):
        k = (str(d.get("id_cuenta")), d.get("unidad"))
        c, p, v = _f(d.get("cantidad")), _f(d.get("precio")), _f(d.get("valuacion"))
        # acumula si hubiera duplicados (no debería: grano único)
        pc, pp, pv = out.get(k, (0.0, 0.0, 0.0))
        out[k] = (pc + c, max(pp, p), pv + v)
    return out


def _sql_dia(fecha: str, cont_ids, cont_names) -> tuple[dict[tuple, tuple], int]:
    """{(id_cuenta, unidad): (cant, prec, val)} ELEGIBLE (post _aum_filters) +
    cuántas filas se excluyeron por los filtros."""
    out: dict[tuple, tuple] = {}
    excluidas = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id_cuenta, cuenta, unidad, cantidad, precio, valuacion "
            "FROM portafolio.tenencia WHERE fecha = %s", (fecha,))
        for idc, cuenta, unidad, cant, prec, val in cur.fetchall():
            if is_excluded(cuenta, unidad, id_cuenta=str(idc),
                           contrapartes_ids=cont_ids, contrapartes_names=cont_names):
                excluidas += 1
                continue
            k = (str(idc), unidad)
            pc, pp, pv = out.get(k, (0.0, 0.0, 0.0))
            out[k] = (pc + _f(cant), max(pp, _f(prec)), pv + _f(val))
    return out, excluidas


def _cmp_dia(mongo: dict, sql: dict) -> dict:
    mk, sk = set(mongo), set(sql)
    match = mk & sk
    diff_c = diff_p = diff_v = 0
    for k in match:
        mc, mp, mv = mongo[k]
        sc, sp, sv = sql[k]
        if round(mc, 2) != round(sc, 2):
            diff_c += 1
        if round(mp, 2) != round(sp, 2):
            diff_p += 1
        if round(mv, 2) != round(sv, 2):
            diff_v += 1
    return {
        "n_mongo": len(mk), "n_sql": len(sk), "match": len(match),
        "diff_cant": diff_c, "diff_precio": diff_p, "diff_val": diff_v,
        "only_mongo": len(mk - sk), "only_sql": len(sk - mk),
    }


def main() -> int:
    aum_col = get_mongo_client_read()["Valuaciones"]["AuM"]
    cont_ids = load_contrapartes_id_cuentas()
    cont_names = load_contrapartes_names()

    una = _opt("--fecha")
    if una:
        fechas = [una]
    else:
        fm, fs = _fechas_mongo(aum_col), _fechas_sql()
        inter = sorted(fm & fs)
        print("=" * 86)
        print("MEDICIÓN sync Mongo(AuM) ← SQL(portafolio.tenencia) · READ-ONLY")
        print("=" * 86)
        print(f"  fechas en Mongo AuM:        {len(fm)}")
        print(f"  fechas en SQL portafolio:   {len(fs)}")
        print(f"  intersección (a sincronizar): {len(inter)}")
        print(f"  solo en SQL (no en Mongo):  {len(fs - fm)}  → {sorted(fs - fm)[:8]}")
        print("=" * 86)
        fechas = inter

    print(f"  {'FECHA':<12} {'MONGO':>7} {'SQL':>7} {'MATCH':>7} "
          f"{'≠CANT':>7} {'≠PREC':>7} {'≠VAL':>7} {'DEL':>6} {'INS':>6} {'excl':>6}")
    print("  " + "-" * 82)
    tot = {"n_mongo": 0, "n_sql": 0, "match": 0, "diff_cant": 0, "diff_precio": 0,
           "diff_val": 0, "only_mongo": 0, "only_sql": 0, "excl": 0}
    for fecha in fechas:
        mongo = _mongo_dia(aum_col, fecha)
        sql, excl = _sql_dia(fecha, cont_ids, cont_names)
        r = _cmp_dia(mongo, sql)
        r["excl"] = excl
        for k in tot:
            tot[k] += r.get(k, 0)
        print(f"  {fecha:<12} {r['n_mongo']:>7} {r['n_sql']:>7} {r['match']:>7} "
              f"{r['diff_cant']:>7} {r['diff_precio']:>7} {r['diff_val']:>7} "
              f"{r['only_mongo']:>6} {r['only_sql']:>6} {excl:>6}")
    print("  " + "-" * 82)
    print(f"  {'TOTAL':<12} {tot['n_mongo']:>7} {tot['n_sql']:>7} {tot['match']:>7} "
          f"{tot['diff_cant']:>7} {tot['diff_precio']:>7} {tot['diff_val']:>7} "
          f"{tot['only_mongo']:>6} {tot['only_sql']:>6} {tot['excl']:>6}")
    print("=" * 86)
    print("Lectura:")
    print("  ≠CANT/≠PREC/≠VAL = (cuenta,unidad) que matchean pero el valor difiere → lo que el sync CORRIGE.")
    print("  DEL = en Mongo y no en SQL elegible → el sync los borraría (mal cargados/stale).")
    print("  INS = en SQL y no en Mongo → el sync los insertaría, PERO sin tipoTitulo (decisión pendiente).")
    print("(NO se escribió nada — read-only.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
