"""Cliente SEC EDGAR con rate limiting y User-Agent identificable.

Fuente oficial pública de filings de la SEC. Endpoints que exponemos:

    - get_submissions(cik)         → lista completa de filings de una empresa
    - list_filings(cik, form="..") → idem, filtrado por form-type (helper)
    - get_filing_index(cik, acc)   → lista de archivos en un filing específico
    - get_file(cik, acc, filename) → descarga raw de un archivo del filing
    - get_quarterly_index(year, quarter, form_filter=None) → form.idx parseado

SEC permite **10 req/s** con User-Agent identificable. Acá usamos 8 req/s
para tener margen ante ráfagas. Auth: ninguna, pero el header User-Agent
es OBLIGATORIO — sin él (o con uno genérico tipo "python-requests/...")
SEC bloquea con 403.

User-Agent configurable vía env `SEC_EDGAR_USER_AGENT`. Si falta usamos un
default identificable para que SEC sepa quiénes somos.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

import requests

logger = logging.getLogger(__name__)

USER_AGENT = os.getenv(
    "SEC_EDGAR_USER_AGENT",
    "TradingAV Research mollonicolas95@gmail.com",
)

# 8/s nos da margen sobre el 10/s real. Un job de 90 filings × 2 requests
# (index + xml) toma ~25s sin chocarse con el límite.
MAX_CALLS_PER_SEC = 8
_rate_lock = threading.Lock()
_calls_ts: list[float] = []


class SECError(RuntimeError):
    """Error del cliente SEC (red, rate limit, respuesta inválida)."""


def _wait_for_rate_limit() -> None:
    """Bloquea si llegamos al cap del segundo actual. Thread-safe."""
    with _rate_lock:
        now = time.time()
        _calls_ts[:] = [t for t in _calls_ts if now - t < 1.0]
        if len(_calls_ts) >= MAX_CALLS_PER_SEC:
            sleep_s = 1.0 - (now - _calls_ts[0]) + 0.05
            if sleep_s > 0:
                time.sleep(sleep_s)
                now = time.time()
                _calls_ts[:] = [t for t in _calls_ts if now - t < 1.0]
        _calls_ts.append(now)


def _get(
    url: str,
    params: dict | None = None,
    accept: str = "application/json",
    timeout: int = 20,
) -> requests.Response:
    """GET con throttle + headers SEC-compliant. Devuelve Response raw porque
    algunos endpoints devuelven JSON, otros XML, otros HTML — el caller
    decide cómo parsearlo.
    """
    _wait_for_rate_limit()
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        raise SECError(f"red: {e}") from e
    if r.status_code == 403:
        raise SECError(
            "403 Forbidden — SEC bloqueó el User-Agent. "
            "Revisar SEC_EDGAR_USER_AGENT en .env."
        )
    if r.status_code == 429:
        raise SECError("429 Rate Limit — esperá un minuto y reintentá.")
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Wrappers por endpoint
# ─────────────────────────────────────────────────────────────────────────────


def get_submissions(cik: str) -> dict:
    """Lista completa de filings de una empresa.

    Args:
        cik: con o sin zero-padding. Se normaliza a 10 dígitos zero-padded
             (formato que pide SEC).

    Returns:
        Dict de submissions.json. Campos relevantes top-level:
            - name, tickers, exchanges, cik
            - filings.recent.{accessionNumber, filingDate, reportDate, form,
              primaryDocument, isXBRL, ...}  (listas paralelas)
            - filings.files  (chunks de filings más viejos — paginar si hace falta)

    Raises:
        SECError si la respuesta no es 200.
    """
    cik_padded = cik.lstrip("0").zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    r = _get(url)
    if r.status_code != 200:
        raise SECError(f"HTTP {r.status_code} en submissions: {r.text[:200]}")
    return r.json()


def list_filings(cik: str, form_prefix: str | None = None) -> list[dict]:
    """Lista filings del bucket `recent` de un CIK, opcionalmente filtrados.

    Args:
        cik: company CIK.
        form_prefix: si se pasa, solo devuelve filings cuyo `form` empieza
            con este prefijo. Ej. 'form_prefix=13F' matchea 13F-HR,
            13F-HR/A, 13F-NT.

    Returns:
        Lista de dicts con campos planos:
            {accession, filingDate, reportDate, form, primaryDocument, isXBRL, size}

    Nota: solo devuelve el bucket `recent` (~1000 filings más recientes).
    Para histórico profundo habría que paginar `filings.files`.
    """
    j = get_submissions(cik)
    recent = (j.get("filings") or {}).get("recent") or {}
    accs = recent.get("accessionNumber") or []
    if not accs:
        return []

    rows: list[dict] = []
    for i, acc in enumerate(accs):
        form = (recent.get("form") or [""])[i]
        if form_prefix and not form.startswith(form_prefix):
            continue
        rows.append({
            "accession":        acc,
            "filingDate":       (recent.get("filingDate") or [""])[i],
            "reportDate":       (recent.get("reportDate") or [""])[i],
            "form":             form,
            "primaryDocument":  (recent.get("primaryDocument") or [""])[i],
            "isXBRL":           (recent.get("isXBRL") or [0])[i],
            "size":             (recent.get("size") or [0])[i],
        })
    return rows


def get_filing_index(cik: str, accession: str) -> dict:
    """Lista de archivos en un filing específico.

    Args:
        cik:       company CIK (con o sin padding).
        accession: '0001193125-26-054580' (con o sin guiones).

    Returns:
        {'directory': {'item': [{'name', 'size', 'type'}, ...], 'name', ...}}
    """
    cik_clean = str(int(cik.lstrip("0")))
    acc_clean = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{acc_clean}/index.json"
    r = _get(url)
    if r.status_code != 200:
        raise SECError(f"HTTP {r.status_code} en index {accession}: {r.text[:200]}")
    return r.json()


def get_file(cik: str, accession: str, filename: str) -> bytes:
    """Descarga un archivo del filing como bytes raw.

    Args:
        cik:       company CIK.
        accession: con o sin guiones.
        filename:  ej. 'primary_doc.xml' o '50240.xml' — viene del index.

    Returns:
        Bytes del archivo. El caller parsea como XML/HTML/lo que sea.
    """
    cik_clean = str(int(cik.lstrip("0")))
    acc_clean = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{acc_clean}/{filename}"
    r = _get(url, accept="*/*")
    if r.status_code != 200:
        raise SECError(f"HTTP {r.status_code} en file {filename}: {r.text[:200]}")
    return r.content


# ─────────────────────────────────────────────────────────────────────────────
# Quarterly full-index — para discovery cross-CIK (ej. todos los 13F-HR de un Q)
# ─────────────────────────────────────────────────────────────────────────────


_ACCESSION_RE = re.compile(r"(\d{10}-\d{2}-\d{6})")

# Parser basado en regex en lugar de fixed-width — SEC cambia los anchos
# de columna de tanto en tanto (ej. form_type expandido a 17 chars cuando
# sumaron forms más largos como "SCHEDULE 13G/A"). Match por patrones
# (CIK = dígitos, fecha = YYYY-MM-DD, file = edgar/) es estable.
#
# Captura:
#   form     : desde el inicio hasta 2+ espacios (permite forms con espacios
#              internos tipo "NT 10-K")
#   company  : hasta 2+ espacios antes del CIK numérico
#   cik      : 1-10 dígitos
#   date     : YYYY-MM-DD
#   file     : edgar/data/...
_IDX_LINE_RE = re.compile(
    r"^(\S.*?)\s{2,}"                        # form_type
    r"(\S.*?)\s{2,}"                         # company_name
    r"(\d{1,10})\s+"                         # cik
    r"(\d{4}-\d{2}-\d{2})\s+"                # date_filed
    r"(edgar/data/.+)$"                      # file_name
)


def _parse_idx_line(line: str) -> dict | None:
    """Parsea UNA línea del form.idx via regex.

    Devuelve None para header, separators, líneas vacías o cualquier cosa
    que no matchee el patrón canónico de un filing row.
    """
    if not line or len(line) < 30:
        return None
    m = _IDX_LINE_RE.match(line)
    if not m:
        return None
    form, company, cik, date_filed, file_name = m.groups()
    acc_match = _ACCESSION_RE.search(file_name)
    return {
        "form":        form.strip(),
        "company":     company.strip(),
        "cik":         cik.lstrip("0") or "0",
        "date_filed":  date_filed,
        "file_name":   file_name,
        "accession":   acc_match.group(1) if acc_match else None,
    }


def get_quarterly_index(
    year: int,
    quarter: int,
    form_filter: str | None = None,
) -> list[dict]:
    """Descarga el form.idx de un trimestre y devuelve filings parseados.

    SEC publica un índice maestro por trimestre que lista TODOS los filings
    presentados en ese período, ordenados por form-type. Útil para discovery
    cross-CIK (ej. "todos los 13F-HR de Q3 2025" sin saber los CIKs).

    Args:
        year:        ej. 2025.
        quarter:     1, 2, 3 ó 4.
        form_filter: si se pasa, solo devuelve filings cuyo form-type
                     empiece con este string (ej. "13F" matchea
                     13F-HR / 13F-HR/A / 13F-NT).

    Returns:
        Lista de dicts: {form, company, cik, date_filed, file_name, accession}

    Raises:
        SECError si la respuesta HTTP no es 200.
    """
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"quarter inválido: {quarter} (debe ser 1-4)")
    url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/form.idx"
    # form.idx es texto plano, 5-15 MB típico. Timeout más generoso.
    r = _get(url, accept="text/plain", timeout=60)
    if r.status_code != 200:
        raise SECError(f"HTTP {r.status_code} en form.idx {year}Q{quarter}: {r.text[:200]}")
    text = r.text

    # El header tiene ~10 líneas + una línea de '---'. Las líneas pre-header
    # nunca parsean (faltan campos), así que el filtro de _parse_idx_line ya
    # las descarta solo. Pero iterar el header (chico) es trivial.
    rows: list[dict] = []
    for line in text.split("\n"):
        parsed = _parse_idx_line(line)
        if not parsed:
            continue
        if form_filter and not parsed["form"].startswith(form_filter):
            continue
        rows.append(parsed)
    return rows
