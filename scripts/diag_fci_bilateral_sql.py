"""scripts/diag_fci_bilateral_sql.py — ¿el FCI bilateral escrito a Mongo está en
el espejo SQL? (read-only).

Contexto: jobs/fci_bilateral.py escribe a CashFlow.Operaciones (Mongo), pero las
vistas ya LEEN de SQL (operaciones). El FCI bilateral solo aparece en las vistas
si sync_postgres lo replicó. Este diag compara Mongo vs SQL para mercado='FCI
Bilateral' (total + hoy + últimos días) → si SQL viene atrás, faltó el sync.

Uso (en el Droplet):
    python -m scripts.diag_fci_bilateral_sql
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read
from core.postgres import connect

MERCADO = "FCI Bilateral"


def main() -> int:
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()  # ART
    hoy_s = hoy.isoformat()
    mdb = get_mongo_client_read()["CashFlow"]["Operaciones"]

    mongo_total = mdb.count_documents({"mercado": MERCADO})
    mongo_hoy = mdb.count_documents({"mercado": MERCADO, "concertacion": hoy_s})
    mongo_bruto = next(iter(mdb.aggregate([
        {"$match": {"mercado": MERCADO}},
        {"$group": {"_id": None, "b": {"$sum": {"$abs": {"$ifNull": ["$bruto", 0]}}}}},
    ])), {}).get("b", 0)

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM operaciones WHERE mercado = %s", (MERCADO,))
        sql_total = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM operaciones WHERE mercado = %s AND concertacion = %s",
                    (MERCADO, hoy_s))
        sql_hoy = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(abs(COALESCE(bruto,0))),0) FROM operaciones WHERE mercado = %s",
                    (MERCADO,))
        sql_bruto = cur.fetchone()[0]
        cur.execute(
            "SELECT concertacion::text, count(*) FROM operaciones WHERE mercado = %s "
            "GROUP BY concertacion ORDER BY concertacion DESC LIMIT 7", (MERCADO,))
        sql_dias = cur.fetchall()

    print(f"FCI Bilateral — hoy (ART) = {hoy_s}\n")
    print(f"  {'':14}{'MONGO':>12}{'SQL':>12}")
    print(f"  {'docs total':14}{mongo_total:>12,}{sql_total:>12,}"
          f"{'  ⚠ dif' if mongo_total != sql_total else '  ✓'}")
    print(f"  {'docs HOY':14}{mongo_hoy:>12,}{sql_hoy:>12,}"
          f"{'  ⚠ dif' if mongo_hoy != sql_hoy else '  ✓'}")
    print(f"  {'Σ|bruto|':14}{mongo_bruto:>12,.0f}{float(sql_bruto):>12,.0f}")

    print("\n  Últimos días en SQL (concertacion → docs):")
    for f, n in sql_dias:
        print(f"    {f}  {n}")

    print("\nLISTO (read-only).")
    if mongo_total != sql_total or mongo_hoy != sql_hoy:
        print("⚠ SQL viene atrás de Mongo → faltó/quedó pendiente el sync_postgres "
              "(corré `python -m jobs.sync_postgres` o esperá el cron de 20 min).")
    else:
        print("✓ Mongo y SQL alineados — el FCI bilateral sincronizó bien.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
