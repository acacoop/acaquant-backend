"""diag_cuentas_descubiertas.py — por qué faltan cuentas en OPERAR.

Read-only. Inspecciona Operaciones.AccountsDescubiertas (la llena jobs.descubrir_cuentas,
que itera 1→12000 ascendente y CORTA a los 60 min → si no llega al final, las cuentas
altas/nuevas quedan sin descubrir).

    python -m scripts.diag_cuentas_descubiertas              # salud general
    python -m scripts.diag_cuentas_descubiertas --cuenta 1234  # + chequea una puntual
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuenta", default=None, help="account_id que falta, para chequearlo")
    args = ap.parse_args()

    col = get_mongo_client_read()["Operaciones"]["AccountsDescubiertas"]
    docs = list(col.find({}, {"_id": 0, "account_id": 1, "last_discovered_at": 1, "activa": 1}))
    if not docs:
        print("⚠ AccountsDescubiertas VACÍA — el job nunca corrió.")
        return 0

    ids = [int(d["account_id"]) for d in docs if str(d.get("account_id", "")).isdigit()]
    ids.sort()
    print(f"Total cuentas descubiertas: {len(docs):,}")
    print(f"account_id min={min(ids)}  max={max(ids)}  (rango del job: 1-12000)")

    art_hoy = (datetime.now(UTC) - timedelta(hours=3)).replace(
        tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    hoy_ids = []
    for d in docs:
        ts = d.get("last_discovered_at")
        if isinstance(ts, datetime):
            tsn = ts.replace(tzinfo=None) if ts.tzinfo else ts
            if tsn >= art_hoy:
                if str(d.get("account_id", "")).isdigit():
                    hoy_ids.append(int(d["account_id"]))
    if hoy_ids:
        print(f"\nDescubiertas HOY: {len(hoy_ids):,}  → el último run llegó hasta "
              f"account_id={max(hoy_ids)}")
        if max(hoy_ids) < 12000:
            print(f"  ⚠ CORTÓ en {max(hoy_ids)} (de 12000) → las cuentas con id > {max(hoy_ids)} "
                  f"NO se refrescaron hoy. ESTE es el bug: el cap de 60 min corta antes del final.")
        else:
            print("  ✅ llegó al final del rango (12000).")
    else:
        print("\n⚠ NINGUNA descubierta hoy → el job de hoy no corrió o falló al arranque.")

    # Las 10 más altas + cuándo se vieron por última vez.
    altas = sorted(docs, key=lambda d: int(d["account_id"]) if str(d.get("account_id","")).isdigit() else 0,
                   reverse=True)[:10]
    print("\nLas 10 cuentas de id más alto descubiertas + última vez vista:")
    for d in altas:
        print(f"   account_id={d.get('account_id')}  last_discovered={d.get('last_discovered_at')}"
              f"  activa={d.get('activa')}")

    if args.cuenta:
        doc = col.find_one({"account_id": args.cuenta}, {"_id": 0})
        c = args.cuenta
        cint = int(c) if str(c).isdigit() else None
        print(f"\n── Cuenta {c} ──")
        if doc:
            print(f"  ✅ ESTÁ en la colección: {doc}")
        elif cint and cint > 12000:
            print(f"  ⚠ NO está, y {cint} > 12000 → está FUERA del rango del job. "
                  f"Fix: subir --hasta.")
        else:
            print(f"  ⚠ NO está, y {cint} ≤ 12000 → el job no llegó a descubrirla "
                  f"(cortó por tiempo antes de ese id). Fix: que el job termine el rango.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
