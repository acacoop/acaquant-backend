"""scripts/partner_sql_baseline.py — baseline Mongo→SQL del partner_api.

Vuelca el contenido actual de la base Mongo `ACAPortfolio` (Cartera + ApiUsers)
a las tablas SQL `partner.cartera` / `partner.api_users` (sql/schema.sql §PARTNER).
Es el SEED inicial de la migración: después, el dual-write de
`jobs/partner_export.py` (Cartera) y `scripts/partner_user.py` (ApiUsers) mantiene
SQL al día. NO va en `jobs/sync_postgres.py` a propósito — el partner es un dominio
SEPARADO (otra base, otro proceso, dato de un tercero).

Seguridad (REGLA #4 — no escanear prod a ciegas):
  * Lee del SECONDARY (get_mongo_client_read, SECONDARY_PREFERRED) → no compite con
    los motores. ACAPortfolio vive en el mismo cluster M10 que la mesa, así que el
    user rw/ro de la mesa puede leerla (el user READ-ONLY del partner es solo para
    el servicio expuesto).
  * Cartera es chica (~1.7k docs medido) → un solo scan + upsert batcheado.
  * Idempotente: UPSERT por PK. Re-correrlo no duplica. `--dry-run` solo cuenta.

Uso (desde la raíz, en el Droplet):
    python -m scripts.partner_sql_baseline            # seed Cartera + ApiUsers
    python -m scripts.partner_sql_baseline --dry-run  # cuenta, no escribe
"""
from __future__ import annotations

import argparse
from datetime import date, datetime

from core.mongo import get_mongo_client_read
from partner_api import pg

_DB = "ACAPortfolio"
_BATCH = 1000


def _fecha(v) -> date | None:
    """Mongo guarda `fecha` como string 'YYYY-MM-DD' → date. None/inválido → None."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _seed_cartera(db, conn, dry: bool) -> int:
    docs = list(db["Cartera"].find({}, {"_id": 0}))
    n = 0
    rows = []
    for d in docs:
        f = _fecha(d.get("fecha"))
        if f is None or not d.get("id_cuenta") or not d.get("unidad"):
            continue  # PK incompleta → se saltea (no debería pasar en el export)
        rows.append({
            "fecha": f, "id_cuenta": str(d["id_cuenta"]), "unidad": d["unidad"],
            "cuenta": d.get("cuenta"), "cantidad": d.get("cantidad"),
            "precio": d.get("precio"), "valuacion": d.get("valuacion"),
            "exported_at": d.get("exported_at"),
        })
    if dry:
        return len(rows)
    for i in range(0, len(rows), _BATCH):
        conn.cursor().executemany(
            "INSERT INTO partner.cartera "
            "(fecha, id_cuenta, unidad, cuenta, cantidad, precio, valuacion, exported_at) "
            "VALUES (%(fecha)s, %(id_cuenta)s, %(unidad)s, %(cuenta)s, "
            "%(cantidad)s, %(precio)s, %(valuacion)s, %(exported_at)s) "
            "ON CONFLICT (fecha, id_cuenta, unidad) DO UPDATE SET "
            "cuenta = EXCLUDED.cuenta, cantidad = EXCLUDED.cantidad, "
            "precio = EXCLUDED.precio, valuacion = EXCLUDED.valuacion, "
            "exported_at = EXCLUDED.exported_at",
            rows[i:i + _BATCH],
        )
        n += len(rows[i:i + _BATCH])
    conn.commit()
    return n


def _seed_users(db, conn, dry: bool) -> int:
    docs = list(db["ApiUsers"].find({}, {"_id": 0}))
    rows = [
        {
            "username": d["username"], "password_hash": d.get("password_hash"),
            "enabled": bool(d.get("enabled", False)), "created_at": d.get("created_at"),
        }
        for d in docs if d.get("username")
    ]
    if dry:
        return len(rows)
    if rows:
        conn.cursor().executemany(
            "INSERT INTO partner.api_users (username, password_hash, enabled, created_at) "
            "VALUES (%(username)s, %(password_hash)s, %(enabled)s, %(created_at)s) "
            "ON CONFLICT (username) DO UPDATE SET "
            "password_hash = EXCLUDED.password_hash, enabled = EXCLUDED.enabled, "
            "created_at = EXCLUDED.created_at",
            rows,
        )
        conn.commit()
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Baseline Mongo→SQL del partner_api.")
    ap.add_argument("--dry-run", action="store_true", help="cuenta, NO escribe")
    args = ap.parse_args()

    db = get_mongo_client_read()[_DB]
    if args.dry_run:
        # No tocar PG: solo contar lo que leería.
        nc = db["Cartera"].count_documents({})
        nu = db["ApiUsers"].count_documents({})
        print(f"[dry-run] Cartera={nc} docs · ApiUsers={nu} docs (no se escribió nada).")
        return

    pg.ensure_schema()
    with pg.connect() as conn:
        nc = _seed_cartera(db, conn, dry=False)
        nu = _seed_users(db, conn, dry=False)
    print(f"✅ baseline OK — partner.cartera={nc} filas · partner.api_users={nu} filas.")


if __name__ == "__main__":
    main()
