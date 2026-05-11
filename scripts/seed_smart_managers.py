"""Seed inicial de Smart.Managers — los 18 managers del cohort.

Upsert por CIK. Para cada uno, valida contra SEC EDGAR que el CIK corresponde
a una entidad real y muestra el nombre oficial (que puede diferir del nombre
hardcodeado — eso está OK, persistimos los dos).

Idempotente: corré tantas veces como quieras, no duplica.

Uso (en el Droplet):
    python -m scripts.seed_smart_managers
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.mongo import get_mongo_client
from core.sec_edgar import SECError, get_submissions

# (CIK, nombre interno, tipo). El nombre oficial se trae de SEC y se persiste
# además, para tener ambos: el "label" que mostramos en UI y el legal SEC.
MANAGERS: list[tuple[str, str, str]] = [
    # Big institutionals
    ("1067983", "Berkshire Hathaway",       "value"),
    ("1364742", "BlackRock Inc.",           "institutional"),
    ("102909",  "Vanguard Group",           "institutional"),
    ("93751",   "State Street Corp",        "institutional"),
    ("884546",  "Capital Research & Mgmt",  "institutional"),
    # Quant funds
    ("1037389", "Renaissance Technologies", "quant"),
    ("1350694", "Bridgewater Associates",   "quant"),
    ("1423053", "Citadel Advisors",         "quant"),
    ("1029160", "Two Sigma Investments",    "quant"),
    ("1167557", "AQR Capital Mgmt",         "quant"),
    ("1009207", "D.E. Shaw & Co",           "quant"),
    ("1273087", "Millennium Mgmt",          "quant"),
    # Concentrated activists / value
    ("1697748", "ARK Investment Mgmt",      "activist"),
    ("1336528", "Pershing Square Capital",  "activist"),
    ("1079114", "Greenlight Capital",       "activist"),
    ("1170494", "Pabrai Investment Funds",  "value"),
    ("1061165", "Baupost Group",            "value"),
    ("1040272", "Third Point",              "activist"),
]


def run() -> None:
    print(f"Seed Smart.Managers — {len(MANAGERS)} managers del cohort\n")
    db = get_mongo_client()
    col = db["Smart"]["Managers"]

    ok = 0
    skipped = 0
    for cik, label, mgr_type in MANAGERS:
        try:
            sub = get_submissions(cik)
            sec_name = sub.get("name") or ""
        except SECError as e:
            print(f"   ✗ CIK {cik} ({label}): SEC error → {e}")
            skipped += 1
            continue

        if not sec_name:
            print(f"   ✗ CIK {cik} ({label}): SEC no devolvió 'name' — revisar")
            skipped += 1
            continue

        doc = {
            "cik":           cik,
            "name":          label,
            "name_sec":      sec_name,
            "type":          mgr_type,
            "seeded_at":     datetime.now(UTC),
        }
        col.update_one({"cik": cik}, {"$set": doc}, upsert=True)
        print(f"   ✓ {cik:<10} {label:<28} (SEC: {sec_name})")
        ok += 1

    print(f"\nListos: {ok}  |  con error: {skipped}")
    total = col.count_documents({})
    print(f"Smart.Managers ahora tiene {total} documentos.")


if __name__ == "__main__":
    run()
