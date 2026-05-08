"""diagnose_live_coverage.py — diagnóstico de cobertura live para
calcular `valor_actual` desde MarketSnapshot en lugar del AuM.

Para cada unidad con tenencia (último snapshot de Valuaciones.AuM):
  1) ¿Existe doc en Valuaciones.Assets?
  2) ¿Tiene `INSTRUMENTO` seteado y no-placeholder?
  3) ¿Ese INSTRUMENTO existe en Trading.MarketSnapshot?

Output: cuenta por bucket + porcentaje de cobertura + top gaps por
valuación total — para priorizar dónde rellenar metadata.

Uso:
    python -m scripts.diagnose_live_coverage
    python -m scripts.diagnose_live_coverage --top 50    # más detalle
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top", type=int, default=20,
                        help="Cantidad de gaps a listar por bucket (default 20).")
    args = parser.parse_args()

    client = get_mongo_client()
    db_v = client["Valuaciones"]
    db_t = client["Trading"]

    # 1) Última fecha en AuM.
    last = db_v["AuM"].find_one({}, sort=[("fecha_snapshot", -1)],
                                projection={"fecha_snapshot": 1})
    if not last:
        print("No hay docs en Valuaciones.AuM.")
        return 1
    ultima_fecha = last["fecha_snapshot"]
    print(f"Último snapshot AuM: {ultima_fecha}\n")

    # 2) Tenencia consolidada por unidad.
    pipeline = [
        {"$match": {"fecha_snapshot": ultima_fecha, "cantidad": {"$ne": 0}}},
        {"$group": {
            "_id":              "$unidad",
            "n_cuentas":        {"$sum": 1},
            "qty_total":        {"$sum": "$cantidad"},
            "valuacion_total":  {"$sum": "$valuacion"},
            "tipo":             {"$first": "$tipoTitulo"},
        }},
        {"$sort": {"valuacion_total": -1}},
    ]
    tenencia = list(db_v["AuM"].aggregate(pipeline))
    n = len(tenencia)
    if not n:
        print("Sin tenencia en el último snapshot.")
        return 0
    print(f"Unidades distintas con tenencia: {n}")
    val_total = sum(t["valuacion_total"] for t in tenencia)
    print(f"Valuación agregada total:        ${val_total:,.0f}\n")

    # 3) Lookup Assets.
    unidades = [t["_id"] for t in tenencia]
    assets_docs = db_v["Assets"].find(
        {"unidad": {"$in": unidades}},
        {"_id": 0, "unidad": 1, "INSTRUMENTO": 1, "TICKER": 1, "CARTERA": 1},
    )
    asset_by_unidad = {a["unidad"]: a for a in assets_docs}

    placeholders = {"", "NO APLICA"}

    sin_asset:        list = []
    sin_instrumento:  list = []
    con_instrumento:  list = []  # (t, a, instrumento)
    for t in tenencia:
        unidad = t["_id"]
        a = asset_by_unidad.get(unidad)
        if not a:
            sin_asset.append(t)
            continue
        instrumento = (a.get("INSTRUMENTO") or "").strip()
        if not instrumento or instrumento in placeholders:
            sin_instrumento.append((t, a))
        else:
            con_instrumento.append((t, a, instrumento))

    # 4) Lookup MarketSnapshot — ¿el INSTRUMENTO existe? El motor_rofex
    # escribe via UpdateOne({"ticker": ticker}, ...) → la clave es el
    # field `ticker`, NO `_id`.
    instrumentos = list({i for _, _, i in con_instrumento})
    snaps_ids = set(db_t["MarketSnapshot"].distinct("ticker", {"ticker": {"$in": instrumentos}}))
    con_live = [(t, a, i) for t, a, i in con_instrumento if i in snaps_ids]
    sin_live = [(t, a, i) for t, a, i in con_instrumento if i not in snaps_ids]

    val_con_live = sum(t["valuacion_total"] for t, _, _ in con_live)
    pct_n = lambda x: 100 * len(x) / n if n else 0  # noqa: E731
    pct_v = lambda v: 100 * v / val_total if val_total else 0  # noqa: E731

    print("=" * 70)
    print("RESUMEN COBERTURA")
    print("=" * 70)
    print(f"{'Bucket':<40} {'unidades':>10} {'%':>6} {'val %':>7}")
    print("-" * 70)
    print(f"{'Sin doc en Assets':<40} {len(sin_asset):>10} "
          f"{pct_n(sin_asset):>5.0f}% "
          f"{pct_v(sum(t['valuacion_total'] for t in sin_asset)):>6.0f}%")
    print(f"{'Con doc pero INSTRUMENTO vacío':<40} {len(sin_instrumento):>10} "
          f"{pct_n(sin_instrumento):>5.0f}% "
          f"{pct_v(sum(t['valuacion_total'] for t, _ in sin_instrumento)):>6.0f}%")
    print(f"{'Con INSTRUMENTO en MarketSnapshot':<40} {len(con_live):>10} "
          f"{pct_n(con_live):>5.0f}% "
          f"{pct_v(val_con_live):>6.0f}%   ← live OK")
    print(f"{'Con INSTRUMENTO pero NO en feed':<40} {len(sin_live):>10} "
          f"{pct_n(sin_live):>5.0f}% "
          f"{pct_v(sum(t['valuacion_total'] for t, _, _ in sin_live)):>6.0f}%")
    print()
    print(f"COBERTURA LIVE: {pct_n(con_live):.0f}% de unidades · "
          f"{pct_v(val_con_live):.0f}% de la valuación.")
    print()

    if sin_instrumento[:args.top]:
        print(f"-- TOP {args.top} SIN INSTRUMENTO (por valuación) --")
        for t, a in sin_instrumento[:args.top]:
            print(f"  ${t['valuacion_total']:>15,.0f}  "
                  f"cart={(a.get('CARTERA') or '-'):20.20s}  {t['_id']}")
        print()

    if sin_live[:args.top]:
        print(f"-- TOP {args.top} CON INSTRUMENTO PERO SIN FEED --")
        for t, a, i in sin_live[:args.top]:
            print(f"  ${t['valuacion_total']:>15,.0f}  "
                  f"inst={i:35.35s}  {t['_id']}")
        print()

    if sin_asset[:args.top]:
        print(f"-- TOP {args.top} SIN DOC EN ASSETS --")
        for t in sin_asset[:args.top]:
            print(f"  ${t['valuacion_total']:>15,.0f}  {t['_id']}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
