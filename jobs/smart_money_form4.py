"""Ingest de Form 4 (insider transactions) para cada CEDEAR del catálogo.

Por cada ticker en Smart.CEDEARsCatalog:
  1. list_filings(cik_issuer, form_prefix="4") — Form 4 + Form 4/A
  2. Filtra a filings con filing_date >= MIN_FILING_DATE
  3. Filtra a filings que NO están en Smart.Form4Transactions (idempotente)
  4. Para cada filing nuevo:
       - get_filing_index → ubica el XML real (no la versión XSL-rendered HTML)
       - get_file → bytes raw del XML
       - parse_form4_xml → extrae issuer + reporting owner + transacciones
       - persiste 1 doc por transacción en Smart.Form4Transactions

El XML de Form 4 (schema X0306) tiene:
  - <issuer>: CIK + name + trading symbol
  - <reportingOwner>: insider (CIK + name + roles + officerTitle)
  - <nonDerivativeTable>: trades de acciones directas
  - <derivativeTable>: opciones, RSUs, derivativos

Para "muchos insiders" persistimos AMBAS tablas, marcadas con tx_type.
Cada transacción tiene un tx_code SEC: P=Purchase, S=Sale, A=Award, M=Exercise,
F=Tax withholding, G=Gift, D=Disposition.

Idempotente: idx único por (accession, tx_type, tx_idx). Re-corridas solo
agregan filings nuevos.

Uso:
    python -m jobs.smart_money_form4                # todos los CEDEARs
    python -m jobs.smart_money_form4 --limit 3      # solo primeros 3 (smoke)
    python -m jobs.smart_money_form4 --ticker AAPL  # un único ticker
"""
from __future__ import annotations

import argparse
import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, UpdateOne

from core.mongo import get_mongo_client
from core.sec_edgar import (
    SECError,
    get_file,
    get_filing_index,
    list_filings,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("Form4Job")

MIN_FILING_DATE = "2025-07-01"

# Códigos SEC que nos interesan especialmente (P/S = open-market voluntarios).
# Los demás se persisten igual pero el frontend los puede filtrar visualmente.
TX_CODES_DESCRIPTION = {
    "P": "Purchase (open market)",
    "S": "Sale (open market)",
    "A": "Award/Grant",
    "M": "Exercise of derivative",
    "F": "Tax withholding",
    "G": "Gift",
    "D": "Disposition",
    "C": "Conversion",
    "V": "Voluntary transaction",
    "J": "Other",
    "K": "Equity swap",
    "L": "Small acquisition",
    "U": "Tender of shares",
    "W": "Inheritance",
    "X": "Exercise of in-the-money option",
    "Z": "Other voluntary",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_indexes(col) -> None:
    """Idempotente — si los índices ya existen no hace nada."""
    col.create_index(
        [("accession", ASCENDING), ("tx_type", ASCENDING), ("tx_idx", ASCENDING)],
        unique=True,
    )
    col.create_index([("issuer_cik", ASCENDING), ("filing_date", ASCENDING)])
    col.create_index([("ticker_cedear", ASCENDING), ("transaction_date", ASCENDING)])
    col.create_index([("insider_name", ASCENDING)])
    col.create_index([("tx_code", ASCENDING)])


def _bool(text: str | None) -> bool | None:
    """SEC usa '1'/'0' o 'true'/'false'. None si vino vacío."""
    if text is None:
        return None
    t = text.strip().lower()
    if t in ("1", "true"):
        return True
    if t in ("0", "false"):
        return False
    return None


def _float(text: str | None) -> float | None:
    if text is None or text.strip() == "":
        return None
    try:
        return float(text.strip())
    except (ValueError, TypeError):
        return None


def _text_at(elem: ET.Element | None, path: str) -> str | None:
    """Devuelve el .text del path o None. path debe terminar en /value
    para los wrappers SEC."""
    if elem is None:
        return None
    found = elem.find(path)
    return found.text.strip() if (found is not None and found.text) else None


def _find_form4_xml_file(items: list[dict]) -> str | None:
    """En el index.json de un Form 4 filing, identifica el archivo XML que
    contiene el ownershipDocument.

    SEC convención:
      - Archivos `.xml` en el root (no en subdirectorios tipo xslF345*) son
        el dato raw.
      - Si solo hay `xslF345X06/form4.xml`, ese MISMO archivo es el XML —
        SEC lo procesa con XSL para mostrar HTML en browser pero la URL
        directa devuelve el XML (verificar con Accept: application/xml).
      - Preferencia: root primero, después xsl.
    """
    candidates_root: list[str] = []
    candidates_xsl: list[str] = []
    for it in items:
        name = (it.get("name") or "")
        if not name.endswith(".xml"):
            continue
        if "/" in name:
            candidates_xsl.append(name)
        else:
            candidates_root.append(name)

    # Preferimos root, luego xsl. Si hay varios root, preferimos los que
    # contengan 'form4', 'ownership' o 'primary_doc'.
    def _score(name: str) -> int:
        lname = name.lower()
        if "form4" in lname:
            return 3
        if "ownership" in lname:
            return 2
        if "primary_doc" in lname:
            return 1
        return 0

    if candidates_root:
        candidates_root.sort(key=_score, reverse=True)
        return candidates_root[0]
    if candidates_xsl:
        return candidates_xsl[0]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────────────


def _parse_owner(ro: ET.Element) -> dict[str, Any]:
    """Extrae datos del <reportingOwner>."""
    oid = ro.find("reportingOwnerId")
    rel = ro.find("reportingOwnerRelationship")
    return {
        "insider_cik":          _text_at(oid, "rptOwnerCik") or "",
        "insider_name":         _text_at(oid, "rptOwnerName") or "",
        "is_director":          _bool(_text_at(rel, "isDirector")),
        "is_officer":           _bool(_text_at(rel, "isOfficer")),
        "is_ten_percent_owner": _bool(_text_at(rel, "isTenPercentOwner")),
        "is_other":             _bool(_text_at(rel, "isOther")),
        "officer_title":        _text_at(rel, "officerTitle") or "",
    }


def _parse_non_derivative(tx: ET.Element) -> dict[str, Any]:
    """Extrae una <nonDerivativeTransaction>."""
    coding = tx.find("transactionCoding")
    amts = tx.find("transactionAmounts")
    post = tx.find("postTransactionAmounts")
    ownership = tx.find("ownershipNature")

    shares = _float(_text_at(amts, "transactionShares/value"))
    price = _float(_text_at(amts, "transactionPricePerShare/value"))
    return {
        "tx_type":              "non_derivative",
        "security_title":       _text_at(tx, "securityTitle/value") or "",
        "transaction_date":     _text_at(tx, "transactionDate/value"),
        "tx_code":              _text_at(coding, "transactionCode") or "",
        "tx_form_type":         _text_at(coding, "transactionFormType") or "",
        "shares":               shares,
        "price_per_share":      price,
        "acquired_disposed":    _text_at(amts, "transactionAcquiredDisposedCode/value"),
        "value_usd":            round(shares * price, 2) if (shares and price) else None,
        "shares_owned_after":   _float(_text_at(post, "sharesOwnedFollowingTransaction/value")),
        "ownership_direct":     (_text_at(ownership, "directOrIndirectOwnership/value") == "D"),
    }


def _parse_derivative(tx: ET.Element) -> dict[str, Any]:
    """Extrae una <derivativeTransaction> (opciones, RSUs, etc.)."""
    coding = tx.find("transactionCoding")
    amts = tx.find("transactionAmounts")
    post = tx.find("postTransactionAmounts")
    ownership = tx.find("ownershipNature")
    underlying = tx.find("underlyingSecurity")

    shares = _float(_text_at(amts, "transactionShares/value"))
    price = _float(_text_at(amts, "transactionPricePerShare/value"))
    return {
        "tx_type":              "derivative",
        "security_title":       _text_at(tx, "securityTitle/value") or "",
        "conversion_price":     _float(_text_at(tx, "conversionOrExercisePrice/value")),
        "transaction_date":     _text_at(tx, "transactionDate/value"),
        "tx_code":              _text_at(coding, "transactionCode") or "",
        "tx_form_type":         _text_at(coding, "transactionFormType") or "",
        "shares":               shares,
        "price_per_share":      price,
        "acquired_disposed":    _text_at(amts, "transactionAcquiredDisposedCode/value"),
        "value_usd":            round(shares * price, 2) if (shares and price) else None,
        "shares_owned_after":   _float(_text_at(post, "sharesOwnedFollowingTransaction/value")),
        "ownership_direct":     (_text_at(ownership, "directOrIndirectOwnership/value") == "D"),
        "underlying_security_title":  _text_at(underlying, "underlyingSecurityTitle/value") or "",
        "underlying_shares":          _float(_text_at(underlying, "underlyingSecurityShares/value")),
    }


def parse_form4_xml(xml_bytes: bytes, filing: dict, cedear: dict) -> list[dict]:
    """Parse un Form 4 XML. Devuelve list of transaction docs (1 por trade)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"XML mal formado: {e}") from e

    # Si lo que vino no es un ownershipDocument (ej. HTML servido por XSL),
    # detectamos y fallamos rápido.
    if root.tag != "ownershipDocument":
        raise ValueError(f"root tag inesperado: {root.tag} (no es Form 4 XML)")

    issuer = root.find("issuer")
    issuer_cik = _text_at(issuer, "issuerCik") or ""
    issuer_name = _text_at(issuer, "issuerName") or ""
    issuer_ticker = _text_at(issuer, "issuerTradingSymbol") or ""

    # Form 4 puede tener múltiples reportingOwners (raro pero existe).
    # Usamos el primero como insider principal — la mayoría de filings 1 owner.
    owners = root.findall("reportingOwner")
    owner_data = _parse_owner(owners[0]) if owners else {}

    period = _text_at(root, "periodOfReport") or ""
    is_amendment = (filing.get("form") or "").endswith("/A")

    transactions: list[dict] = []
    # Non-derivative table
    for tx in root.findall(".//nonDerivativeTable/nonDerivativeTransaction"):
        transactions.append(_parse_non_derivative(tx))
    # Derivative table (opciones, RSUs)
    for tx in root.findall(".//derivativeTable/derivativeTransaction"):
        transactions.append(_parse_derivative(tx))

    docs: list[dict] = []
    for i, tx in enumerate(transactions):
        doc = {
            "accession":         filing["accession"],
            "tx_idx":            i,
            "issuer_cik":        issuer_cik,
            "issuer_name":       issuer_name,
            "issuer_ticker":     issuer_ticker,
            "ticker_cedear":     cedear["ticker"],
            "cusip":             cedear.get("cusip"),
            "filing_date":       filing.get("filingDate"),
            "period_of_report":  period,
            "form":              filing.get("form"),
            "is_amendment":      is_amendment,
            **owner_data,
            **tx,
            "persisted_at":      datetime.now(UTC),
        }
        docs.append(doc)
    return docs


# ─────────────────────────────────────────────────────────────────────────────
# Ingestor
# ─────────────────────────────────────────────────────────────────────────────


def _already_ingested(col, accession: str) -> bool:
    return col.count_documents({"accession": accession}, limit=1) > 0


def _fetch_xml_bytes(cik: str, accession: str) -> bytes | None:
    """Encuentra y descarga el XML real del Form 4. None si no se encuentra."""
    try:
        idx = get_filing_index(cik, accession)
    except SECError as e:
        logger.warning("    index error %s: %s", accession, e)
        return None
    items = (idx.get("directory") or {}).get("item") or []
    xml_name = _find_form4_xml_file(items)
    if not xml_name:
        logger.warning("    %s: no XML file en index", accession)
        return None
    try:
        return get_file(cik, accession, xml_name)
    except SECError as e:
        logger.warning("    get_file error %s/%s: %s", accession, xml_name, e)
        return None


def ingest_cedear(cedear: dict, col, *, dry_run: bool = False) -> dict[str, int]:
    """Procesa todos los Form 4 de UN cedear. Devuelve contadores."""
    ticker = cedear["ticker"]
    cik = cedear.get("cik_issuer")
    stats = {"filings_total": 0, "filings_new": 0, "tx_inserted": 0, "skipped": 0, "errors": 0}
    if not cik:
        logger.warning("[%s] sin cik_issuer — skip", ticker)
        return stats

    try:
        filings = list_filings(cik, form_prefix="4")
    except SECError as e:
        logger.error("[%s] list_filings error: %s", ticker, e)
        stats["errors"] += 1
        return stats

    # Filter to cutoff + only forms 4 / 4/A (excluye 40-F, 40-APP, etc.)
    eligible = [
        f for f in filings
        if f.get("form") in ("4", "4/A")
        and (f.get("filingDate") or "") >= MIN_FILING_DATE
    ]
    stats["filings_total"] = len(eligible)
    logger.info("[%s] %d Form 4 elegibles desde %s", ticker, len(eligible), MIN_FILING_DATE)

    ops: list[UpdateOne] = []
    for filing in eligible:
        if _already_ingested(col, filing["accession"]):
            stats["skipped"] += 1
            continue
        xml_bytes = _fetch_xml_bytes(cik, filing["accession"])
        if not xml_bytes:
            stats["errors"] += 1
            continue
        try:
            docs = parse_form4_xml(xml_bytes, filing, cedear)
        except (ValueError, ET.ParseError) as e:
            logger.warning("[%s] %s parse error: %s", ticker, filing["accession"], e)
            stats["errors"] += 1
            continue
        if not docs:
            # Form 4 sin transacciones (raro pero pasa: position-only changes).
            stats["filings_new"] += 1
            continue
        stats["filings_new"] += 1
        stats["tx_inserted"] += len(docs)
        for d in docs:
            ops.append(UpdateOne(
                {
                    "accession": d["accession"],
                    "tx_type":   d["tx_type"],
                    "tx_idx":    d["tx_idx"],
                },
                {"$set": d},
                upsert=True,
            ))
        # Flush every 100 ops para no acumular en memoria.
        if len(ops) >= 100 and not dry_run:
            col.bulk_write(ops, ordered=False)
            ops.clear()

    if ops and not dry_run:
        col.bulk_write(ops, ordered=False)
    return stats


def run(ticker_filter: str | None = None, limit: int | None = None, dry_run: bool = False) -> None:
    client = get_mongo_client()
    catalog = client["Smart"]["CEDEARsCatalog"]
    col = client["Smart"]["Form4Transactions"]
    _ensure_indexes(col)

    filtro = {"is_active": True}
    if ticker_filter:
        filtro["ticker"] = ticker_filter.upper()
    cedears = list(catalog.find(filtro, {"_id": 0}))
    if limit:
        cedears = cedears[:limit]

    if not cedears:
        logger.warning("Sin CEDEARs en el catalog. Corré scripts.seed_cedears_catalog antes.")
        return

    logger.info(
        "Ingest Form 4 desde %s — %d CEDEARs %s",
        MIN_FILING_DATE, len(cedears), "(DRY RUN)" if dry_run else "",
    )

    totals = {"filings_total": 0, "filings_new": 0, "tx_inserted": 0, "skipped": 0, "errors": 0}
    for c in cedears:
        s = ingest_cedear(c, col, dry_run=dry_run)
        for k in totals:
            totals[k] += s[k]

    logger.info("─" * 60)
    logger.info(
        "TOTALES — filings elegibles=%d  nuevos=%d  ya_estaban=%d  "
        "transacciones=%d  errores=%d",
        totals["filings_total"], totals["filings_new"], totals["skipped"],
        totals["tx_inserted"], totals["errors"],
    )
    if not dry_run:
        total_db = col.count_documents({})
        logger.info("Smart.Form4Transactions ahora tiene %d documentos.", total_db)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ticker", type=str, default=None,
        help="Solo procesar este ticker (ej. AAPL). Default: todos.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limitar a N primeros CEDEARs del catalog (para smoke testing).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Solo fetcha y parsea, no escribe a Mongo.",
    )
    args = parser.parse_args()
    run(ticker_filter=args.ticker, limit=args.limit, dry_run=args.dry_run)
