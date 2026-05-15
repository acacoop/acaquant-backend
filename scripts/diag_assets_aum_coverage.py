"""diag_assets_aum_coverage.py — cobertura de Assets vs AuM.

Valida si las unidades que aparecen en Valuaciones.AuM existen en:
  - Valuaciones.Assets   (fuente de verdad UPPERCASE, lo que edita Manager)
  - TitulosAPI.AssetsAPI (copia derivada lowercase)

Contexto del bug: jobs/aum.py::_sincronizar_assets upsertea las unidades
nuevas del snapshot diario en TitulosAPI.AssetsAPI, NO en
Valuaciones.Assets. Y scripts/api_migrate.py::migrate_assets dropea +
reconstruye AssetsAPI desde Valuaciones.Assets. Resultado sospechado:
las unidades nuevas del AuM nunca llegan a Valuaciones.Assets -> Manager
nunca las ve para categorizar; y las que aum.py mete en AssetsAPI se
pierden en la proxima migracion.

NO modifica nada. Solo reporta.

Corre:  python -m scripts.diag_assets_aum_coverage
"""
from __future__ import annotations

from core.mongo import get_mongo_client

_EMPTY: list[str | None] = ["", "NO APLICA", None]


def main() -> None:
    cli = get_mongo_client()
    db_val = cli["Valuaciones"]
    db_tit = cli["TitulosAPI"]

    # ── 1. Unidades del ultimo snapshot de AuM + historicas ────────────
    ultimo = db_val["AuM"].find_one(
        {}, sort=[("fecha_snapshot", -1)], projection={"fecha_snapshot": 1},
    )
    fecha_ult = ultimo.get("fecha_snapshot") if ultimo else None
    print("=" * 72)
    print(f"Ultimo fecha_snapshot en Valuaciones.AuM: {fecha_ult!r}")

    unidades_ult = (
        set(db_val["AuM"].distinct("unidad", {"fecha_snapshot": fecha_ult}))
        if fecha_ult else set()
    )
    unidades_hist = set(db_val["AuM"].distinct("unidad"))
    print(f"Unidades en el ULTIMO snapshot:           {len(unidades_ult)}")
    print(f"Unidades HISTORICAS (todos los snapshots): {len(unidades_hist)}")

    # ── 2. Unidades en cada coleccion de Assets ────────────────────────
    unidades_assets = set(db_val["Assets"].distinct("unidad"))
    unidades_api = set(db_tit["AssetsAPI"].distinct("unidad"))
    print()
    print(f"Unidades en Valuaciones.Assets:   {len(unidades_assets)}")
    print(f"Unidades en TitulosAPI.AssetsAPI: {len(unidades_api)}")

    # ── 3. Faltantes ───────────────────────────────────────────────────
    falt_assets_ult = unidades_ult - unidades_assets
    falt_assets_hist = unidades_hist - unidades_assets
    falt_api_ult = unidades_ult - unidades_api

    print()
    print("=" * 72)
    print(f"Unidades del ULTIMO snapshot que FALTAN en Valuaciones.Assets: "
          f"{len(falt_assets_ult)}")
    for u in sorted(falt_assets_ult)[:40]:
        print(f"   - {u}")
    if len(falt_assets_ult) > 40:
        print(f"   ... y {len(falt_assets_ult) - 40} mas")

    print()
    print(f"Unidades HISTORICAS que FALTAN en Valuaciones.Assets: "
          f"{len(falt_assets_hist)}")
    print(f"Unidades del ULTIMO snapshot que FALTAN en TitulosAPI.AssetsAPI: "
          f"{len(falt_api_ult)}")

    # ── 4. Gaps de metadata UPPERCASE en Valuaciones.Assets ────────────
    print()
    print("=" * 72)
    print("Gaps de metadata en Valuaciones.Assets (campo vacio / 'NO APLICA' / null):")
    total_assets = db_val["Assets"].count_documents({})
    print(f"   (total docs en Valuaciones.Assets: {total_assets})")
    for campo in ("CARTERA", "EMISOR", "CLASE_ACTIVO", "CALIFICACION",
                  "TICKER", "VENCIMIENTO", "INSTRUMENTO"):
        n = db_val["Assets"].count_documents({campo: {"$in": _EMPTY}})
        print(f"   {campo:14} vacio: {n}")

    # ── 5. Veredicto ───────────────────────────────────────────────────
    print()
    print("=" * 72)
    if falt_assets_hist:
        print(f"CONFIRMADO: {len(falt_assets_hist)} unidades de AuM NO estan en "
              f"Valuaciones.Assets.")
        print("El auto-agregado de jobs/aum.py apunta a TitulosAPI.AssetsAPI, no")
        print("al origen Valuaciones.Assets -> Manager nunca las ve.")
    else:
        print("Todas las unidades historicas de AuM estan en Valuaciones.Assets.")
        print("(El auto-agregado podria estar OK, o se completo a mano alguna vez.)")


if __name__ == "__main__":
    main()
