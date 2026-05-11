"""Seed inicial de Smart.Managers — 14 managers con cara/visión propia.

Cohort enfocado en "alpha-seeking" managers: value (Buffett, Klarman, Pabrai),
activists (Ackman, Einhorn, Loeb, Wood) y quants legendarios (Simons, Dalio,
Griffin, etc.). NO incluye index trackers institucionales (BlackRock, Vanguard,
State Street, Capital Research) — esos manejan $T de pasivo, no aportan signal.

Para cada CIK valida contra SEC:
  1. Que la entidad exista (submissions.json devuelve 200 + name).
  2. Que tenga 13F-HR filings >= 2025-07-01 (cutoff fiscal del MVP).

Si una entidad tiene 0 13Fs desde el cutoff → marca ✗ y el CIK probablemente
está mal. Vos me decís qué managers tienen ✗ y los reemplazo.

Idempotente: upsert por CIK.

Uso:
    python -m scripts.seed_smart_managers
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.mongo import get_mongo_client
from core.sec_edgar import SECError, get_submissions, list_filings

# Cutoff fiscal del MVP — solo cuenta 13Fs filed desde acá.
CUTOFF_DATE = "2025-07-01"

# (CIK, nombre interno, tipo, "cara" que representa).
# CIKs verificados/corregidos respecto a la lista anterior:
#   - Two Sigma: 1029160 era SOROS → corregido a 1179392
#   - Pabrai:    1170494 era random → corregido a 1259618
#   - Baupost:   1061165 era Lone Pine → corregido a 1061160
#   - Third Point: 1040272 era Greenlight LLC → corregido a 1040273
# Si alguno todavía está mal, el script lo detecta por count_13fs=0.
MANAGERS: list[tuple[str, str, str, str]] = [
    # Value (alpha-seekers fundamentales)
    ("1067983", "Berkshire Hathaway",       "value",    "Warren Buffett"),
    ("1259618", "Pabrai Investment Funds",  "value",    "Mohnish Pabrai"),
    ("1061160", "Baupost Group",            "value",    "Seth Klarman"),
    # Activists / concentrated bets
    ("1336528", "Pershing Square Capital",  "activist", "Bill Ackman"),
    ("1079114", "Greenlight Capital",       "activist", "David Einhorn"),
    ("1040273", "Third Point",              "activist", "Dan Loeb"),
    ("1697748", "ARK Investment Mgmt",      "activist", "Cathie Wood"),
    # Quant legends
    ("1037389", "Renaissance Technologies", "quant",    "Jim Simons (legacy)"),
    ("1350694", "Bridgewater Associates",   "quant",    "Ray Dalio (legacy)"),
    ("1423053", "Citadel Advisors",         "quant",    "Ken Griffin"),
    ("1179392", "Two Sigma Investments",    "quant",    "Siegel & Overdeck"),
    ("1167557", "AQR Capital Mgmt",         "quant",    "Cliff Asness"),
    ("1009207", "D.E. Shaw & Co",           "quant",    "David E. Shaw (founder)"),
    ("1273087", "Millennium Mgmt",          "quant",    "Izzy Englander"),
]


def _count_13fs_since(cik: str, cutoff: str) -> int:
    """Cuenta 13F-HR (cualquier variante) filed en o después de cutoff (YYYY-MM-DD)."""
    try:
        filings = list_filings(cik, form_prefix="13F")
    except SECError:
        return 0
    return sum(1 for f in filings if f.get("filingDate", "") >= cutoff)


def run() -> None:
    print(f"Seed Smart.Managers — {len(MANAGERS)} managers (cutoff 13F filings: {CUTOFF_DATE})\n")
    db = get_mongo_client()
    col = db["Smart"]["Managers"]

    ok = 0
    flagged = 0
    for cik, label, mgr_type, persona in MANAGERS:
        try:
            sub = get_submissions(cik)
            sec_name = sub.get("name") or ""
        except SECError as e:
            print(f"   ✗ {cik:<10} {label:<28} SEC error: {e}")
            flagged += 1
            continue

        n_13fs = _count_13fs_since(cik, CUTOFF_DATE)
        if n_13fs == 0:
            print(
                f"   ✗ {cik:<10} {label:<28} SEC name='{sec_name[:40]}' "
                f"→ 0 13Fs desde {CUTOFF_DATE} — CIK probablemente equivocado"
            )
            flagged += 1
            continue

        doc = {
            "cik":        cik,
            "name":       label,
            "name_sec":   sec_name,
            "type":       mgr_type,
            "persona":    persona,
            "n_13fs_since_cutoff": n_13fs,
            "seeded_at":  datetime.now(UTC),
        }
        col.update_one({"cik": cik}, {"$set": doc}, upsert=True)
        print(
            f"   ✓ {cik:<10} {label:<28} ({persona}) "
            f"— {n_13fs} 13Fs desde {CUTOFF_DATE}"
        )
        ok += 1

    print(f"\nListos: {ok}  |  con problemas: {flagged}")
    total = col.count_documents({})
    print(f"Smart.Managers ahora tiene {total} documentos.")
    if flagged:
        print(
            f"\n⚠ {flagged} manager(s) con CIK probablemente errado. "
            "Pasame los nombres marcados con ✗ y los busco con la "
            "herramienta de search de SEC."
        )


if __name__ == "__main__":
    run()
