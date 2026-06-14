"""scripts/ver_aum_sql.py — PRIMER RESULTADO: serie de AuM de un fondo desde SQL.

Replica EXACTO el cálculo del endpoint /api/operaciones/flujo-vs-aum (la "vista de
AUM en NEGOCIO") pero leyendo de SQL `portafolio.tenencia` (fechas corregidas) en
vez de Mongo `Valuaciones.AuM`, y los muestra LADO A LADO por mes para que veas la
diferencia. Read-only, no toca la API ni escribe nada.

Las exclusiones se aplican con la MISMA función `_aum_filters.is_excluded` que usa
el job → el SQL queda manzana-con-manzana con Mongo (salvo las correcciones de fecha,
que es justo lo que queremos ver).

Uso:
    python -m scripts.ver_aum_sql "Nombre del Fondo (contraparte)"
    python -m scripts.ver_aum_sql --listar          # lista fondos disponibles
"""
from __future__ import annotations

import sys
from collections import defaultdict

from api.services.titulos_flujos import assets_normalizados
from core.mongo import get_mongo_client_read
from core.postgres import get_pool
from jobs._aum_filters import (
    is_excluded,
    load_contrapartes_id_cuentas,
    load_contrapartes_names,
)


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _unidades_fondo(contraparte: str) -> list[str]:
    """Unidades FCI del emisor — idéntico al endpoint flujo_vs_aum."""
    return [
        d["unidad"]
        for d in assets_normalizados()
        if d.get("cartera") in ("FCI", "CARTERA FCI")
        and d.get("emisor") == contraparte and d.get("unidad")
    ]


def _fondos_disponibles() -> list[str]:
    return sorted({
        d["emisor"]
        for d in assets_normalizados()
        if d.get("cartera") in ("FCI", "CARTERA FCI") and d.get("emisor")
    })


def _mensual_ultimo(por_fecha: dict[str, float]) -> dict[str, float]:
    """{fecha_iso: total} → {mes 'YYYY-MM': total del ÚLTIMO día del mes presente}.
    Mismo criterio que el $group con $last del endpoint."""
    ult: dict[str, str] = {}
    for fecha in por_fecha:
        mes = fecha[:7]
        if mes not in ult or fecha > ult[mes]:
            ult[mes] = fecha
    return {mes: por_fecha[fecha] for mes, fecha in ult.items()}


def _serie_mongo(unidades: list[str]) -> dict[str, float]:
    db_v = get_mongo_client_read()["Valuaciones"]
    cur = db_v["AuM"].aggregate([
        {"$match": {"unidad": {"$in": unidades}, "valuacion": {"$ne": None}}},
        {"$group": {"_id": "$fecha_snapshot", "total": {"$sum": "$valuacion"}}},
    ])
    return _mensual_ultimo({str(r["_id"]): _f(r["total"]) for r in cur if r.get("_id")})


def _serie_sql(unidades: list[str], cont_ids, cont_names) -> dict[str, float]:
    por_fecha: dict[str, float] = defaultdict(float)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT fecha::text, id_cuenta, cuenta, unidad, valuacion "
            "FROM portafolio.tenencia "
            "WHERE unidad = ANY(%s) AND valuacion IS NOT NULL",
            (unidades,))
        for fecha, idc, cuenta, unidad, val in cur.fetchall():
            if is_excluded(cuenta, unidad, id_cuenta=str(idc),
                           contrapartes_ids=cont_ids, contrapartes_names=cont_names):
                continue
            por_fecha[fecha] += _f(val)
    return _mensual_ultimo(dict(por_fecha))


def main() -> int:
    if "--listar" in sys.argv:
        fondos = _fondos_disponibles()
        print(f"Fondos disponibles ({len(fondos)}):")
        for f in fondos:
            print(f"  · {f}")
        return 0

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print('Uso: python -m scripts.ver_aum_sql "Nombre del Fondo"   (o --listar)')
        return 1
    contraparte = args[0]

    unidades = _unidades_fondo(contraparte)
    if not unidades:
        print(f"Sin unidades FCI para '{contraparte}'. Probá --listar.")
        return 1

    cont_ids = load_contrapartes_id_cuentas()
    cont_names = load_contrapartes_names()

    s_mongo = _serie_mongo(unidades)
    s_sql = _serie_sql(unidades, cont_ids, cont_names)

    print("=" * 70)
    print(f"AuM mensual · {contraparte}")
    print(f"  unidades FCI: {len(unidades)}  ·  {unidades}")
    print("  MONGO = Valuaciones.AuM (hoy) · SQL = portafolio.tenencia (corregido)")
    print("=" * 70)
    print(f"  {'MES':<9} {'MONGO':>18} {'SQL':>18} {'Δ %':>9}")
    print("  " + "-" * 58)
    for mes in sorted(set(s_mongo) | set(s_sql)):
        m, s = s_mongo.get(mes, 0.0), s_sql.get(mes, 0.0)
        dpct = ((s - m) / m * 100) if m else float("nan")
        flag = "  ⚠" if abs(s - m) > 0.01 * max(abs(m), 1) else ""
        print(f"  {mes:<9} {m:>18,.2f} {s:>18,.2f} {dpct:>8.1f}%{flag}")
    print("=" * 70)
    print("(read-only — no se escribió nada; Mongo y la API quedan intactos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
