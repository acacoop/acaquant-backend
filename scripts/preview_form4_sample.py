"""Preview SAMPLE de lo que insertaría jobs.smart_money_form4 — sin tocar Mongo.

Toma el Form 4 más reciente de UN ticker (AAPL por default) desde 2025-07-01,
descarga el XML, lo parsea con la misma función del job real, y pretty-prints
los docs resultantes. **No escribe nada** en Mongo.

Útil como sanity check antes de correr el job completo.

Uso:
    python -m scripts.preview_form4_sample
    python -m scripts.preview_form4_sample --ticker NVDA
    python -m scripts.preview_form4_sample --ticker TSLA --idx 0
        (idx=0 es el más reciente; idx=1 el segundo más reciente; etc.)
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime

from core.mongo import get_mongo_client_read
from core.sec_edgar import list_filings
from jobs.smart_money_form4 import (
    MIN_FILING_DATE,
    _fetch_xml_bytes,
    parse_form4_xml,
)


def _serialize(o):
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def run(ticker: str, idx: int) -> None:
    db = get_mongo_client_read()
    cedear = db["Smart"]["CEDEARsCatalog"].find_one({"ticker": ticker})
    if not cedear:
        print(f"✗ Ticker {ticker} no está en Smart.CEDEARsCatalog.")
        print("  Corré antes: python -m scripts.seed_cedears_catalog")
        return

    cik = cedear.get("cik_issuer")
    if not cik:
        print(f"✗ {ticker} no tiene cik_issuer en el catalog.")
        return

    print(f"→ Buscando Form 4 #{idx} más reciente de {ticker} (CIK {cik})\n")
    filings = list_filings(cik, form_prefix="4")
    eligible = [
        f for f in filings
        if f.get("form") in ("4", "4/A")
        and (f.get("filingDate") or "") >= MIN_FILING_DATE
    ]
    print(f"   {len(eligible)} Form 4 elegibles desde {MIN_FILING_DATE}\n")

    if not eligible:
        print("✗ Sin Form 4 elegibles. Probá con otro ticker o relajá el cutoff.")
        return
    if idx >= len(eligible):
        print(f"✗ idx={idx} fuera de rango (hay {len(eligible)})")
        return

    filing = eligible[idx]
    print(f"   Usando: form={filing['form']}  filed={filing['filingDate']}")
    print(f"           accession={filing['accession']}")
    print(f"           primaryDocument={filing.get('primaryDocument')}\n")

    xml_bytes = _fetch_xml_bytes(cik, filing["accession"])
    if not xml_bytes:
        print("✗ No pude bajar el XML del filing.")
        return
    print(f"   ✓ XML descargado: {len(xml_bytes):,} bytes\n")

    print("─" * 80)
    print("PRIMEROS 600 CHARS DEL XML RAW (para verificar shape):")
    print("─" * 80)
    head = xml_bytes[:600].decode("utf-8", errors="replace")
    for line in head.splitlines():
        print(f"  {line}")
    print("  ...")
    print()

    try:
        docs = parse_form4_xml(xml_bytes, filing, cedear)
    except (ValueError, Exception) as e:
        print(f"✗ Parser error: {type(e).__name__}: {e}")
        return

    print("=" * 80)
    print(f"PARSEADO — {len(docs)} transacción(es) extraídas")
    print("Esto es EXACTAMENTE lo que el job insertaría en Smart.Form4Transactions:")
    print("=" * 80)
    for i, d in enumerate(docs, 1):
        print(f"\n──── DOC {i}/{len(docs)} ────")
        print(json.dumps(d, indent=2, default=_serialize, ensure_ascii=False))

    if not docs:
        print("\n   (este filing tiene 0 transacciones — pasa con Form 4 de ")
        print("    'position-only' changes. El job lo cuenta pero no inserta nada.)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="AAPL", help="Ticker del catalog (default: AAPL)")
    parser.add_argument("--idx", type=int, default=0, help="0=más reciente, 1=segundo, etc.")
    args = parser.parse_args()
    run(args.ticker.upper(), args.idx)
