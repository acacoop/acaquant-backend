"""Ingest universal de 13F-HR filtrado al CEDEAR universe.

Para cada quarter pedido:
  1. Descarga form.idx → todas las entradas 13F* del trimestre
  2. Filtra a 13F-HR + 13F-HR/A con filing_date >= MIN_FILING_DATE
  3. Skip filings ya ingestados (idempotente por accession)
  4. Para cada filing nuevo:
       - get_filing_index → ubica el information table XML
       - get_file → bytes del XML
       - parse_13f_xml → list de holdings
       - filtra a los CUSIPs del CEDEAR catalog
       - persiste 1 doc por línea matched en Smart.Holdings13F
       - auto-registra el manager en Smart.Managers (discovery sin hardcode)

Outputs:
  - Smart.Holdings13F      → 1 doc por (filer_cik, cusip, accession, line_idx)
  - Smart.Managers         → managers descubiertos (cik, name, n_filings, ...)

Idempotente: si una accession ya está en Smart.Holdings13F, se skipea.

CLI:
  python -m jobs.smart_money_13f --quarters 2025q4,2026q1
  python -m jobs.smart_money_13f --quarters 2026q1 --limit 100   # smoke (100 filings)
  python -m jobs.smart_money_13f --quarters 2026q1 --dry-run     # no escribir nada

Importante:
- Cada filing requiere 2 requests a SEC (index + xml). Throttle interno 8 req/s.
- 9,500 filings por quarter = ~40 min cada quarter.
- Si se interrumpe (Ctrl+C, kill), re-corriendo retoma desde donde quedó.
- Nota técnica: SEC cambió el formato del field `value` en 13F el 3 de enero
  2023. Antes era miles de USD; ahora es USD directo. Cutoff 2025-07-01 está
  100% en el nuevo formato, así que NO multiplicamos por 1000.
"""
from __future__ import annotations

import argparse
import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from pymongo import ASCENDING, UpdateOne

from core.mongo import get_mongo_client
from core.sec_edgar import (
    SECError,
    get_file,
    get_filing_index,
    get_quarterly_index,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("Job13F")

MIN_FILING_DATE = "2025-07-01"
BULK_FLUSH_EVERY = 200
PROGRESS_LOG_EVERY = 100


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _int(s) -> int:
    try:
        return int((s or "0").replace(",", ""))
    except (ValueError, TypeError, AttributeError):
        return 0


def _parse_quarter(q_str: str) -> tuple[int, int]:
    """'2026q1' → (2026, 1)."""
    q_str = q_str.strip().lower()
    if "q" not in q_str:
        raise ValueError(f"formato esperado YYYYqN: {q_str!r}")
    y_s, q_s = q_str.split("q")
    return int(y_s), int(q_s)


def _find_infotable_xml(items: list[dict]) -> str | None:
    """En el index.json de un 13F, identifica el XML de holdings.
    Convención: XML en root (no subfolder xsl*), skip primary_doc, prefer mayor tamaño.
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
    """Parse information table XML del 13F. Devuelve holdings raw (dict por row).
    Tolera namespaces variables iterando por local-tag.
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


# ─────────────────────────────────────────────────────────────────────────────
# Mongo helpers
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_indexes(holdings_col, managers_col) -> None:
    holdings_col.create_index(
        [("accession", ASCENDING), ("cusip", ASCENDING), ("line_idx", ASCENDING)],
        unique=True,
    )
    holdings_col.create_index([("filer_cik", ASCENDING), ("filing_date", ASCENDING)])
    holdings_col.create_index([("cusip", ASCENDING), ("filing_date", ASCENDING)])
    holdings_col.create_index([("ticker_cedear", ASCENDING), ("filing_date", ASCENDING)])
    managers_col.create_index([("cik", ASCENDING)], unique=True)


def _load_cedear_universe(client) -> dict[str, dict]:
    docs = list(client["Smart"]["CEDEARsCatalog"].find(
        {"is_active": True},
        {"_id": 0, "ticker": 1, "cusip": 1, "nombre_corto": 1},
    ))
    return {d["cusip"]: d for d in docs}


def _already_ingested(holdings_col, accession: str) -> bool:
    return holdings_col.count_documents({"accession": accession}, limit=1) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Fetch + parse pipeline para un filing
# ─────────────────────────────────────────────────────────────────────────────


def _fetch_xml_bytes(cik: str, accession: str) -> bytes | None:
    try:
        idx = get_filing_index(cik, accession)
    except SECError as e:
        logger.warning("    %s/%s: index error: %s", cik, accession, e)
        return None
    items = (idx.get("directory") or {}).get("item") or []
    xml_name = _find_infotable_xml(items)
    if not xml_name:
        return None
    try:
        return get_file(cik, accession, xml_name)
    except SECError as e:
        logger.warning("    %s/%s/%s: get_file error: %s", cik, accession, xml_name, e)
        return None


def _build_holding_docs(filing: dict, holdings: list[dict], cedear_by_cusip: dict) -> list[dict]:
    """Filtra holdings al CEDEAR universe y construye los docs para Mongo."""
    now = datetime.now(UTC)
    docs: list[dict] = []
    for line_idx, h in enumerate(holdings):
        cusip = (h.get("cusip") or "").strip()
        if cusip not in cedear_by_cusip:
            continue
        cedear = cedear_by_cusip[cusip]
        docs.append({
            "filer_cik":             filing["cik"],
            "filer_name":            filing["company"],
            "accession":             filing["accession"],
            "filing_date":           filing["date_filed"],
            "form":                  filing["form"],
            "is_amendment":          filing["form"].endswith("/A"),
            "cusip":                 cusip,
            "ticker_cedear":         cedear["ticker"],
            "line_idx":              line_idx,
            "name_of_issuer":        h.get("nameOfIssuer", ""),
            "title_of_class":        h.get("titleOfClass", ""),
            "shares":                _int(h.get("sshPrnamt")),
            "shares_type":           h.get("sshPrnamtType"),
            "value_usd":             _int(h.get("value")),  # post-2023: USD directos
            "investment_discretion": h.get("investmentDiscretion"),
            "persisted_at":          now,
        })
    return docs


def _upsert_manager(managers_col, filing: dict, n_matches: int) -> None:
    """Auto-register/update del manager en Smart.Managers."""
    managers_col.update_one(
        {"cik": filing["cik"]},
        {
            "$set": {
                "cik":              filing["cik"],
                "name":             filing["company"],
                "last_filing_date": filing["date_filed"],
                "last_seen_at":     datetime.now(UTC),
            },
            "$max": {"max_n_cedear_holdings": n_matches},
            "$inc": {"n_filings_seen": 1},
            "$setOnInsert": {
                "first_seen_at":    datetime.now(UTC),
                "discovered_via":   "smart_money_13f",
            },
        },
        upsert=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def run(quarters: list[str], limit: int | None, dry_run: bool) -> None:
    client = get_mongo_client()
    holdings_col = client["Smart"]["Holdings13F"]
    managers_col = client["Smart"]["Managers"]

    if not dry_run:
        _ensure_indexes(holdings_col, managers_col)

    cedear_by_cusip = _load_cedear_universe(client)
    if not cedear_by_cusip:
        logger.error("Smart.CEDEARsCatalog vacío. Corré scripts.seed_cedears_catalog primero.")
        return
    logger.info("CEDEAR universe: %d CUSIPs activos", len(cedear_by_cusip))

    quarter_pairs = [_parse_quarter(q) for q in quarters]
    logger.info("Quarters a procesar: %s", quarter_pairs)
    logger.info("Cutoff filing_date: >= %s", MIN_FILING_DATE)
    if dry_run:
        logger.info("(DRY RUN — no se escribe a Mongo)")

    totals = {
        "quarter_filings":      0,
        "filings_new":          0,
        "filings_skipped":      0,
        "filings_no_match":     0,
        "holdings_inserted":    0,
        "managers_discovered":  set(),
        "errors":               0,
    }

    for year, q in quarter_pairs:
        logger.info("─" * 70)
        logger.info("Procesando %d Q%d — bajando form.idx ...", year, q)
        try:
            entries = get_quarterly_index(year, q, form_filter="13F")
        except SECError as e:
            logger.error("error bajando form.idx %dQ%d: %s", year, q, e)
            continue

        hr_entries = [
            e for e in entries
            if e["form"] in ("13F-HR", "13F-HR/A")
            and (e.get("date_filed") or "") >= MIN_FILING_DATE
        ]
        logger.info("  %d entradas 13F* totales en el quarter", len(entries))
        logger.info("  %d son 13F-HR/HR-A desde %s — procesando", len(hr_entries), MIN_FILING_DATE)

        if limit:
            hr_entries = hr_entries[:limit]
            logger.info("  (--limit=%d, recortando)", limit)

        ops_buffer: list[UpdateOne] = []
        for i, filing in enumerate(hr_entries, 1):
            if i % PROGRESS_LOG_EVERY == 0 or i == len(hr_entries):
                logger.info(
                    "  %d/%d  | filings_new=%d skipped=%d no_match=%d errors=%d  | holdings_inserted=%d  managers=%d",
                    i, len(hr_entries),
                    totals["filings_new"], totals["filings_skipped"],
                    totals["filings_no_match"], totals["errors"],
                    totals["holdings_inserted"], len(totals["managers_discovered"]),
                )

            if not filing.get("accession"):
                totals["errors"] += 1
                continue

            if not dry_run and _already_ingested(holdings_col, filing["accession"]):
                totals["filings_skipped"] += 1
                continue

            xml_bytes = _fetch_xml_bytes(filing["cik"], filing["accession"])
            if not xml_bytes:
                totals["errors"] += 1
                continue

            try:
                holdings = parse_13f_xml(xml_bytes)
            except ValueError as e:
                logger.warning("  %s: parse error: %s", filing["accession"], e)
                totals["errors"] += 1
                continue

            docs = _build_holding_docs(filing, holdings, cedear_by_cusip)
            if not docs:
                # filing válido pero sin matches al CEDEAR universe
                totals["filings_no_match"] += 1
                continue

            totals["filings_new"] += 1
            totals["holdings_inserted"] += len(docs)
            totals["managers_discovered"].add(filing["cik"])

            if not dry_run:
                for d in docs:
                    ops_buffer.append(UpdateOne(
                        {
                            "accession": d["accession"],
                            "cusip":     d["cusip"],
                            "line_idx":  d["line_idx"],
                        },
                        {"$set": d},
                        upsert=True,
                    ))
                _upsert_manager(managers_col, filing, len(docs))

                if len(ops_buffer) >= BULK_FLUSH_EVERY:
                    holdings_col.bulk_write(ops_buffer, ordered=False)
                    ops_buffer.clear()

        if ops_buffer and not dry_run:
            holdings_col.bulk_write(ops_buffer, ordered=False)
            ops_buffer.clear()

        totals["quarter_filings"] += len(hr_entries)

    logger.info("=" * 70)
    logger.info(
        "TOTALES — quarter_filings=%d  new=%d  skipped=%d  no_match=%d  "
        "holdings_inserted=%d  managers_discovered=%d  errors=%d",
        totals["quarter_filings"], totals["filings_new"], totals["filings_skipped"],
        totals["filings_no_match"], totals["holdings_inserted"],
        len(totals["managers_discovered"]), totals["errors"],
    )
    if not dry_run:
        n_h = holdings_col.count_documents({})
        n_m = managers_col.count_documents({})
        logger.info("Smart.Holdings13F ahora tiene %d documentos", n_h)
        logger.info("Smart.Managers ahora tiene %d documentos", n_m)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--quarters", default="2025q4,2026q1",
        help="Comma-separated quarters (default '2025q4,2026q1' = ~80 min)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Procesar solo N filings por quarter (para smoke testing)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="No escribe a Mongo — solo prueba el pipeline",
    )
    args = parser.parse_args()
    quarters = [q.strip() for q in args.quarters.split(",") if q.strip()]
    run(quarters, args.limit, args.dry_run)
