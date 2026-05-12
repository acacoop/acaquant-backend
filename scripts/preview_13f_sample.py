"""Preview SAMPLE de lo que insertaría jobs.smart_money_13f — sin tocar Mongo.

Toma un 13F-HR conocido (Berkshire Q4 2025 por default), descarga su
information table XML, parsea los holdings, los filtra a los 36 CUSIPs
del CEDEAR catalog, y pretty-prints lo que iría a Smart.Holdings13F.

Útil como sanity check del parser antes del job completo.

Uso:
    python -m scripts.preview_13f_sample
    python -m scripts.preview_13f_sample --cik 1336528    # Pershing Square (Ackman)
    python -m scripts.preview_13f_sample --year 2025 --quarter 4
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from datetime import datetime

from core.mongo import get_mongo_client_read
from core.sec_edgar import (
    SECError,
    get_file,
    get_filing_index,
    get_quarterly_index,
)


def _serialize(o):
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def _find_infotable_xml(items: list[dict]) -> str | None:
    """En el index.json de un 13F, identifica el XML de holdings.

    13F filing típicamente tiene:
      - primary_doc.xml       → cover page (filer info, period), sin holdings
      - <N>.xml o <acc>.xml   → information table con holdings

    Heurística: XMLs en root, skip primary_doc, prefiero el más grande.
    """
    candidates: list[tuple[int, str]] = []
    for it in items:
        name = it.get("name") or ""
        if "/" in name or not name.endswith(".xml"):
            continue
        if "primary_doc" in name.lower():
            continue
        try:
            size = int(it.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        candidates.append((size, name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def parse_13f_xml(xml_bytes: bytes) -> list[dict]:
    """Parse information table XML. Devuelve list of holdings raw.

    13F XML usa namespace (suele variar entre filings). Iteramos buscando
    el tag local 'infoTable' sin importar el prefix.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"XML 13F mal formado: {e}") from e

    rows: list[dict] = []
    for elem in root.iter():
        if elem.tag.split("}")[-1] != "infoTable":
            continue
        row: dict[str, str] = {}
        for child in elem:
            tag = child.tag.split("}")[-1]
            if tag == "shrsOrPrnAmt":
                for sub in child:
                    sub_tag = sub.tag.split("}")[-1]
                    row[sub_tag] = (sub.text or "").strip()
            else:
                row[tag] = (child.text or "").strip()
        rows.append(row)
    return rows


def _int(s: str | None) -> int:
    try:
        return int((s or "0").replace(",", ""))
    except (ValueError, TypeError):
        return 0


def run(year: int, quarter: int, cik_filter: str) -> None:
    print(f"→ Bajando form.idx de {year} Q{quarter} …")
    entries = get_quarterly_index(year, quarter, form_filter="13F")
    print(f"   ✓ {len(entries):,} entradas 13F* totales\n")

    hr = [e for e in entries if e["form"] in ("13F-HR", "13F-HR/A")]
    print(f"   {len(hr):,} de esas son 13F-HR / 13F-HR/A (las que contienen holdings)\n")

    # Buscar el filing del CIK pedido. Si hay 13F-HR y 13F-HR/A, preferimos el /A.
    cik_norm = cik_filter.lstrip("0")
    matches = [e for e in hr if e["cik"].lstrip("0") == cik_norm]
    if not matches:
        print(f"✗ No encontré 13F-HR de CIK {cik_filter} en {year}Q{quarter}.")
        print("   Probá otro quarter (--year/--quarter) o cik (--cik).")
        return
    # Si hay amendment, lo preferimos
    filing = next((m for m in matches if m["form"].endswith("/A")), matches[0])

    print("   Usando filing:")
    print(f"     filer:       {filing['company']}")
    print(f"     CIK:         {filing['cik']}")
    print(f"     form:        {filing['form']}")
    print(f"     filed:       {filing['date_filed']}")
    print(f"     accession:   {filing['accession']}\n")

    print("→ Bajando index.json del filing …")
    try:
        idx = get_filing_index(filing["cik"], filing["accession"])
    except SECError as e:
        print(f"✗ {e}")
        return
    items = (idx.get("directory") or {}).get("item") or []
    xml_name = _find_infotable_xml(items)
    if not xml_name:
        print("✗ No encontré XML de holdings en el filing")
        print(f"   archivos en el directorio: {[it.get('name') for it in items]}")
        return
    print(f"   ✓ XML identificado: {xml_name}")

    print("\n→ Bajando información table XML …")
    try:
        xml_bytes = get_file(filing["cik"], filing["accession"], xml_name)
    except SECError as e:
        print(f"✗ {e}")
        return
    print(f"   ✓ {len(xml_bytes):,} bytes")

    try:
        holdings = parse_13f_xml(xml_bytes)
    except ValueError as e:
        print(f"✗ Parser error: {e}")
        return
    print(f"   ✓ {len(holdings)} holdings parseadas del XML\n")

    # Cargar CEDEAR catalog para filtrar
    db = get_mongo_client_read()
    cedear_docs = list(db["Smart"]["CEDEARsCatalog"].find(
        {"is_active": True},
        {"_id": 0, "ticker": 1, "cusip": 1, "nombre_corto": 1},
    ))
    cedear_by_cusip = {c["cusip"]: c for c in cedear_docs}
    print(f"   Filtrando a los {len(cedear_by_cusip)} CUSIPs del CEDEAR catalog …\n")

    # Agregamos por (cusip) — un mismo CUSIP puede aparecer en múltiples sub-entities
    matched_raw: list[dict] = []
    by_cusip: dict[str, dict] = {}
    for h in holdings:
        cusip = (h.get("cusip") or "").strip()
        if cusip not in cedear_by_cusip:
            continue
        matched_raw.append(h)
        agg = by_cusip.setdefault(cusip, {
            "cusip":             cusip,
            "ticker":            cedear_by_cusip[cusip]["ticker"],
            "nombre_corto":      cedear_by_cusip[cusip]["nombre_corto"],
            "name_of_issuer":    h.get("nameOfIssuer", ""),
            "n_lines":           0,
            "shares_total":      0,
            "value_total_usd":   0,  # value_usd_thousands × 1000
        })
        agg["n_lines"] += 1
        agg["shares_total"] += _int(h.get("sshPrnamt"))
        agg["value_total_usd"] += _int(h.get("value")) * 1000

    print("=" * 80)
    print(f"  MATCHES CEDEAR: {len(matched_raw)} líneas raw, {len(by_cusip)} CUSIPs únicos")
    print("=" * 80)
    if not by_cusip:
        print("\n  (Este manager no tiene NINGÚN ticker de tu catálogo en su 13F.)")
        print("   El job real ignoraría este filing y no escribiría nada.")
        return

    aggs = sorted(by_cusip.values(), key=lambda x: -x["value_total_usd"])
    print(f"\n  {'TICKER':<6} {'SHARES':>15} {'VALUE USD':>16} {'LINES':>6}  NAME")
    print("  " + "─" * 78)
    for a in aggs:
        print(
            f"  {a['ticker']:<6} {a['shares_total']:>15,} "
            f"${a['value_total_usd']:>15,.0f} {a['n_lines']:>6}  "
            f"{a['name_of_issuer'][:30]}"
        )

    # JSON sample del primer match — esto es lo que iría a Smart.Holdings13F.
    if matched_raw:
        print("\n" + "=" * 80)
        print("  EJEMPLO — primera fila matched (1 doc por línea del XML):")
        print("  Smart.Holdings13F insertaría docs como este:")
        print("=" * 80 + "\n")
        first = matched_raw[0]
        cedear = cedear_by_cusip[first["cusip"]]
        sample_doc = {
            "filer_cik":          filing["cik"],
            "filer_name":         filing["company"],
            "accession":          filing["accession"],
            "filing_date":        filing["date_filed"],
            "form":               filing["form"],
            "is_amendment":       filing["form"].endswith("/A"),
            "cusip":              first.get("cusip"),
            "ticker_cedear":      cedear["ticker"],
            "name_of_issuer":     first.get("nameOfIssuer"),
            "title_of_class":     first.get("titleOfClass"),
            "shares":             _int(first.get("sshPrnamt")),
            "shares_type":        first.get("sshPrnamtType"),
            "value_usd_thousands": _int(first.get("value")),
            "value_usd":          _int(first.get("value")) * 1000,
            "investment_discretion": first.get("investmentDiscretion"),
        }
        print(json.dumps(sample_doc, indent=2, default=_serialize))

    # Resumen final
    total_value = sum(a["value_total_usd"] for a in aggs)
    print("\n  ── Resumen ──")
    print(f"   El filer reportó {len(holdings)} holdings totales en este 13F-HR")
    print(f"   De esos, {len(matched_raw)} líneas matchean nuestros 36 CEDEARs ({len(by_cusip)} CUSIPs únicos)")
    print(f"   Valor agregado en CEDEARs: ${total_value:,.0f}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--quarter", type=int, default=1)
    parser.add_argument(
        "--cik", default="1067983",
        help="CIK del filer (default 1067983 = Berkshire Hathaway)",
    )
    args = parser.parse_args()
    run(args.year, args.quarter, args.cik)
