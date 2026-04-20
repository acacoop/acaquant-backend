"""seed_soberanos.py — carga masiva de bonos soberanos a Trading.Curvas.

Lee todos los `docs/soberanos/*.json` y hace upsert en `Trading.Curvas` por
`ticker_corto`. Idempotente: re-ejecutable sin duplicar.

Uso:
    python -m scripts.seed_soberanos               # upsert todos
    python -m scripts.seed_soberanos --dry         # preview sin escribir
    python -m scripts.seed_soberanos --only GD30   # solo un ticker

Flujo end-to-end:
    1. Agregar archivo JSON nuevo en docs/soberanos/<TICKER>.json
    2. Correr este script en el Droplet
    3. El cron 23:00 UTC propaga a TitulosAPI.ValuacionesAPI vía migrate_flujos_titulos
"""
import argparse
import json
import sys
from pathlib import Path

from core.mongo import get_mongo_client

SOBERANOS_DIR = Path(__file__).resolve().parent.parent / "docs" / "soberanos"


def cargar_docs(only: str | None = None) -> list[dict]:
    """Lee todos los *.json de docs/soberanos/ y devuelve los docs parseados."""
    if not SOBERANOS_DIR.exists():
        print(f"No existe el directorio: {SOBERANOS_DIR}")
        return []

    docs = []
    for path in sorted(SOBERANOS_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as f:
            doc = json.load(f)

        if only and doc.get("ticker_corto") != only:
            continue

        tc = doc.get("ticker_corto")
        if not tc:
            print(f"  ✘ {path.name}: falta ticker_corto — skip")
            continue

        flujos = doc.get("flujos", [])
        suma_amort = sum(f.get("amortizacion_pct", 0) for f in flujos)
        if flujos and abs(suma_amort - 100) > 0.01:
            print(f"  ⚠  {tc}: suma amortizaciones = {suma_amort} (esperado 100)")

        docs.append(doc)

    return docs


def main():
    ap = argparse.ArgumentParser(description="Upsert de soberanos en Trading.Curvas.")
    ap.add_argument("--dry", action="store_true", help="Preview sin escribir a Mongo.")
    ap.add_argument("--only", type=str, help="Solo un ticker_corto.")
    args = ap.parse_args()

    docs = cargar_docs(only=args.only)
    if not docs:
        print("Sin docs para cargar.")
        sys.exit(0)

    print(f"Docs a upsertear ({len(docs)}):")
    for d in docs:
        print(f"  {d['ticker_corto']:<8} tipo={d.get('tipo'):<10} venc={d.get('fecha_vencimiento')}  flujos={len(d.get('flujos', []))}")

    if args.dry:
        print("\n[--dry] no se escribió nada.")
        return

    client = get_mongo_client()
    col = client["Trading"]["Curvas"]

    n_ins = n_upd = 0
    for d in docs:
        tc = d["ticker_corto"]
        res = col.replace_one({"ticker_corto": tc}, d, upsert=True)
        if res.matched_count:
            n_upd += 1
            print(f"  ↻ {tc} actualizado")
        else:
            n_ins += 1
            print(f"  + {tc} insertado")

    print(f"\nOK: {n_ins} insertados, {n_upd} actualizados.")


if __name__ == "__main__":
    main()
