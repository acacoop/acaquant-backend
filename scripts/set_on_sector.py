"""scripts/set_on_sector.py — clasificar las ONs por sector EN LA BASE.

El sector de cada ON vive en Trading.BondsMaster.sector (lo manejás vos). Este
script te deja setearlo por EMISOR de a tandas, sin editar 57 docs a mano en
Atlas y sin nada hardcodeado: VOS pasás la clasificación.

FLUJO TÍPICO:
  1) Ver qué emisores hay y su sector actual:
        python -m scripts.set_on_sector --list

  2) Asignar un sector a uno o varios emisores (DRY-RUN, no escribe):
        python -m scripts.set_on_sector --sector energia --emisores "YPF" "YPF Luz" "Vista Energy"

  3) Si te gusta lo que muestra, lo mismo con --commit:
        python -m scripts.set_on_sector --sector energia --emisores "YPF" "YPF Luz" --commit

  4) Repetís para finanzas / otros. Después, para que impacte en la vista:
        python -m scripts.seed_curvas_ons --commit --force
        sudo systemctl restart motor_curvas

Buckets que usa la vista: energia / finanzas / otros (podés crear otros, caen
en el tab OTROS hasta que el frontend los conozca).
"""
from __future__ import annotations

import argparse
from collections import Counter

from core.mongo import get_mongo_client, get_mongo_client_read


def _listar() -> None:
    db = get_mongo_client_read()["Trading"]
    docs = list(db["BondsMaster"].find({}, {"_id": 0, "emisor": 1, "sector": 1}))
    por_emisor: dict[str, Counter] = {}
    for d in docs:
        em = (d.get("emisor") or "(sin emisor)")
        por_emisor.setdefault(em, Counter())[d.get("sector") or "(sin sector)"] += 1
    print(f"{len(docs)} ONs · {len(por_emisor)} emisores\n")
    print(f"{'emisor':<26}{'#ONs':>6}  sector(es) actuales")
    print("-" * 64)
    for em in sorted(por_emisor):
        c = por_emisor[em]
        total = sum(c.values())
        sectores = ", ".join(f"{s}×{n}" for s, n in c.items())
        print(f"{em:<26}{total:>6}  {sectores}")
    print("\nAsigná con: --sector <energia|finanzas|otros> --emisores \"YPF\" \"...\"")


def _set(sector: str, emisores: list[str], commit: bool) -> None:
    sector = sector.strip().lower()
    read = get_mongo_client_read()["Trading"]["BondsMaster"]
    afectados = list(read.find(
        {"emisor": {"$in": emisores}}, {"_id": 0, "asset": 1, "emisor": 1, "sector": 1}))
    if not afectados:
        print(f"⚠️  Ningún doc con emisor en {emisores}. Revisá el nombre exacto "
              f"(--list te lo muestra).")
        return

    print(f"sector → '{sector}'  para {len(afectados)} ONs:")
    for d in afectados:
        actual = d.get("sector") or "—"
        print(f"   {d.get('asset'):<8} {d.get('emisor'):<24} {actual} → {sector}")

    if not commit:
        print("\n(DRY-RUN — no se escribió nada. Agregá --commit para aplicar.)")
        return

    res = get_mongo_client()["Trading"]["BondsMaster"].update_many(
        {"emisor": {"$in": emisores}}, {"$set": {"sector": sector}})
    print(f"\n✅ {res.modified_count} docs actualizados (sector='{sector}').")
    print("   Ahora: python -m scripts.seed_curvas_ons --commit --force  +  "
          "restart motor_curvas")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="lista emisores + sector actual")
    ap.add_argument("--sector", help="energia | finanzas | otros")
    ap.add_argument("--emisores", nargs="+", help="uno o más emisores (nombre exacto)")
    ap.add_argument("--commit", action="store_true", help="escribe (default: dry-run)")
    args = ap.parse_args()

    if args.list:
        _listar()
        return
    if not args.sector or not args.emisores:
        ap.error("usá --list, o --sector X --emisores \"...\"")
    _set(args.sector, args.emisores, args.commit)


if __name__ == "__main__":
    main()
