"""Discovery: smart money tracker (13F institucionales + STOCK Act Congress).

Probe end-to-end con Apple (AAPL, CUSIP 037833100) como ticker semilla.

Secciones:
  A. EDGAR full-text search → 13F-HR que mencionan AAPL en los últimos
     trimestres. Confirma que el corpus existe y es indexable.
  B. Berkshire Hathaway (CIK 1067983) → submissions.json filtrado a
     form=13F-HR. Cuántos hay, frecuencia, latencia post-quarter.
  C. Bajar la `informationTable` (XML real de holdings) del último 13F-HR
     de Berkshire y mostrar:
       - estructura del XML
       - 10 holdings más grandes por value
       - confirmación de que AAPL está adentro (filtro por CUSIP)
  D. STOCK Act / Congress trading:
       D1. House — disclosures-clerk.house.gov publica un ZIP anual con
           todas las PTR (Periodic Transaction Reports). Probamos descarga.
       D2. Senate — efdsearch.senate.gov requiere aceptar T&C antes de
           cualquier query. Documentamos el estado.

Rate limit SEC: 10 req/s con User-Agent identificable. Acá < 10 reqs total.
Auth: ninguna en SEC, ninguna en House. Senate complicado.

Uso:
    python -m scripts.probe_smart_money
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

import requests

HEADERS = {
    "User-Agent": "TradingAV Research mollonicolas95@gmail.com",
    "Accept": "application/json",
}

AAPL_CUSIP = "037833100"
BERKSHIRE_CIK = "0001067983"  # Berkshire Hathaway Inc.


# ─────────────────────────────────────────────────────────────────────────────
# A. Full-text search en EDGAR
# ─────────────────────────────────────────────────────────────────────────────


def section_a_fts_aapl_en_13fs() -> None:
    print("=== A. EDGAR full-text search: 13F-HR que mencionan AAPL ===")
    # EDGAR Full-Text Search API: https://efts.sec.gov/LATEST/search-index
    # Devuelve hits con el filing + snippet de contexto.
    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q": '"AAPL"',
        "forms": "13F-HR",
        "dateRange": "custom",
        # Último año de filings — captura 4 trimestres.
        "startdt": "2025-05-01",
        "enddt": "2026-05-11",
    }
    print(f"   GET {url}")
    print(f"   params: {params}")
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        print(f"   HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"   body: {r.text[:300]}")
            return
        j = r.json()
        hits = (j.get("hits") or {})
        total = (hits.get("total") or {}).get("value")
        rows = hits.get("hits") or []
        print(f"   total hits: {total}")
        print(f"   muestra de top {min(5, len(rows))}:")
        for h in rows[:5]:
            src = h.get("_source") or {}
            print(
                f"     • {src.get('file_date')}  "
                f"form={src.get('form')}  "
                f"ciks={src.get('ciks')}  "
                f"display='{src.get('display_names', [None])[0]}'"
            )
    except Exception as e:
        print(f"   error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# B. Berkshire Hathaway — listar 13F-HR históricos
# ─────────────────────────────────────────────────────────────────────────────


def section_b_berkshire_13fs() -> dict | None:
    print("\n=== B. Berkshire Hathaway (CIK 1067983) → 13F-HR históricos ===")
    url = f"https://data.sec.gov/submissions/CIK{BERKSHIRE_CIK}.json"
    print(f"   GET {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        print(f"   HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"   body: {r.text[:300]}")
            return None
        j = r.json()
        print(f"   entity: {j.get('name')}")
        filings = (j.get("filings") or {}).get("recent") or {}
        forms = filings.get("form") or []
        # Filtramos a 13F-HR (la "HR" = Holdings Report; hay también 13F-NT
        # Notice Report cuando un manager reporta cero positions o solo
        # las que también reporta otra entidad).
        idx_13f = [i for i, f in enumerate(forms) if f.startswith("13F")]
        print(f"   13F filings encontrados: {len(idx_13f)}")
        if not idx_13f:
            print("   ⚠ Berkshire no tiene 13F en el bucket recent — probar full=1")
            return None
        print("   Últimos 8 13F:")
        print(f"   {'FECHA':<11} {'FORM':<10} {'REPORT':<11} {'ACCESSION':<22}")
        for i in idx_13f[:8]:
            print(
                f"   {filings['filingDate'][i]:<11} "
                f"{filings['form'][i]:<10} "
                f"{(filings.get('reportDate') or [''])[i]:<11} "
                f"{filings['accessionNumber'][i]:<22}"
            )
        # Retornamos el primer 13F-HR (Holdings Report) para usar en C.
        for i in idx_13f:
            if filings["form"][i] == "13F-HR":
                return {
                    "accession": filings["accessionNumber"][i],
                    "filingDate": filings["filingDate"][i],
                    "reportDate": (filings.get("reportDate") or [""])[i],
                    "cik": str(int(j.get("cik") or 0)),
                }
        return None
    except Exception as e:
        print(f"   error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# C. Parsear el information table del 13F-HR
# ─────────────────────────────────────────────────────────────────────────────


def section_c_parsear_13f(meta: dict | None) -> None:
    print("\n=== C. Parsear el information table (XML de holdings) ===")
    if not meta:
        print("   sin meta de la sección B, salto")
        return
    cik = meta["cik"]
    acc_clean = meta["accession"].replace("-", "")
    # El filing 13F-HR tiene siempre un index.json con la lista de archivos.
    idx_url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/index.json"
    )
    print(f"   GET index: {idx_url}")
    try:
        r = requests.get(idx_url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"   HTTP {r.status_code}, body: {r.text[:300]}")
            return
        items = (r.json().get("directory") or {}).get("item") or []
        print(f"   {len(items)} archivos en el filing:")
        for it in items:
            print(f"     - {it.get('name')}  ({it.get('size')} bytes)")
        # Buscamos el XML de holdings. Convención: termina en _informationtable.xml
        # o tiene "infotable" en el nombre. A veces es el segundo XML del filing.
        info_xml = None
        for it in items:
            name = (it.get("name") or "").lower()
            if name.endswith(".xml") and ("infotable" in name or "information" in name):
                info_xml = it["name"]
                break
        # Fallback: tomar el XML más grande del filing (suele ser el infotable).
        if not info_xml:
            xmls = sorted(
                (it for it in items if (it.get("name") or "").endswith(".xml")),
                key=lambda x: int(x.get("size") or 0),
                reverse=True,
            )
            info_xml = xmls[0]["name"] if xmls else None
        if not info_xml:
            print("   ✗ no encontré XML de infotable en el filing")
            return
        print(f"   information table file: {info_xml}")

        xml_url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{info_xml}"
        )
        print(f"   GET XML: {xml_url}")
        xr = requests.get(xml_url, headers=HEADERS, timeout=20)
        if xr.status_code != 200:
            print(f"   HTTP {xr.status_code}")
            return
        # Parse XML. 13F infotable usa namespace ns1; lo limpiamos.
        root = ET.fromstring(xr.content)
        # Buscamos <infoTable> recursivo (sin importar prefix).
        rows = []
        for elem in root.iter():
            if elem.tag.split("}")[-1] == "infoTable":
                row = {}
                for child in elem:
                    tag = child.tag.split("}")[-1]
                    row[tag] = (child.text or "").strip()
                # shrsOrPrnAmt es nested.
                for child in elem:
                    if child.tag.split("}")[-1] == "shrsOrPrnAmt":
                        for sub in child:
                            row[sub.tag.split("}")[-1]] = (sub.text or "").strip()
                rows.append(row)
        print(f"   total holdings parseados: {len(rows)}")

        # Top 10 por value (valor en MILES, según convención 13F).
        def _val(r: dict) -> int:
            try:
                return int(r.get("value") or 0)
            except Exception:
                return 0

        rows_sorted = sorted(rows, key=_val, reverse=True)
        total_value_k = sum(_val(r) for r in rows)
        print(f"   AUM 13F reportado (sum de value): ${total_value_k:,} mil = ${total_value_k/1000:,.0f}M\n")
        print("   --- Top 10 holdings ---")
        print(f"   {'#':>3} {'CUSIP':<11} {'NAME':<40} {'VALUE($k)':>14} {'SHARES':>14}")
        for i, r in enumerate(rows_sorted[:10], 1):
            print(
                f"   {i:>3} "
                f"{r.get('cusip', ''):<11} "
                f"{r.get('nameOfIssuer', '')[:40]:<40} "
                f"{_val(r):>14,} "
                f"{int(r.get('sshPrnamt') or 0):>14,}"
            )

        # ¿Está AAPL?
        aapl_rows = [r for r in rows if r.get("cusip", "").replace(" ", "") == AAPL_CUSIP]
        print(f"\n   --- AAPL (CUSIP {AAPL_CUSIP}) en el filing ---")
        if not aapl_rows:
            print("   ✗ AAPL NO está en este 13F (raro para Berkshire, pero posible si vendieron)")
        for r in aapl_rows:
            print(
                f"   ✓ name={r.get('nameOfIssuer')}  "
                f"value=${int(r.get('value') or 0):,}k  "
                f"shares={int(r.get('sshPrnamt') or 0):,}  "
                f"type={r.get('sshPrnamtType')}"
            )
    except Exception as e:
        import traceback
        print(f"   error: {e}")
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────────────
# D. Congress trading (House + Senate)
# ─────────────────────────────────────────────────────────────────────────────


def section_d_house_disclosures() -> None:
    print("\n=== D1. House (Representatives) — disclosures-clerk.house.gov ===")
    # disclosures-clerk publica un ZIP anual con todas las PTR.
    # https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2025FD.zip
    # Estructura del ZIP: PDF index + XML con los registros.
    anio = 2025
    url = f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{anio}FD.zip"
    print(f"   GET {url}")
    try:
        r = requests.get(
            url,
            headers={"User-Agent": HEADERS["User-Agent"]},
            timeout=30,
        )
        print(f"   HTTP {r.status_code}  content-length: {len(r.content):,} bytes")
        if r.status_code != 200:
            print(f"   body: {r.text[:200]}")
            return
        # Probamos abrir como ZIP.
        try:
            z = zipfile.ZipFile(io.BytesIO(r.content))
            print(f"   ZIP OK, archivos: {z.namelist()}")
            # Buscamos el XML.
            xml_name = next((n for n in z.namelist() if n.lower().endswith(".xml")), None)
            if xml_name:
                with z.open(xml_name) as f:
                    xml_bytes = f.read()
                print(f"   {xml_name}: {len(xml_bytes):,} bytes")
                # Estructura: <FinancialDisclosure><Member><...>
                root = ET.fromstring(xml_bytes)
                miembros = list(root.iter())
                print(f"   tag raíz: {root.tag}, total elementos: {len(miembros)}")
                # Mostramos las primeras 5 entries.
                children = list(root)
                print(f"   {len(children)} entries de nivel 1")
                if children:
                    sample = children[0]
                    print("   sample entry fields:")
                    for c in sample:
                        print(f"     {c.tag}: {(c.text or '').strip()[:60]}")
        except zipfile.BadZipFile:
            print("   ✗ no es un ZIP válido (puede haber cambiado el formato)")
            print(f"   primeros bytes: {r.content[:80]}")
    except Exception as e:
        print(f"   error: {e}")


def section_d_senate_disclosures() -> None:
    print("\n=== D2. Senate (Senators) — efdsearch.senate.gov ===")
    # El portal requiere POST a /search/home/ con cookies de aceptar T&C.
    # No tiene API JSON. Probamos solo la home pública para confirmar acceso.
    url = "https://efdsearch.senate.gov/search/home/"
    print(f"   GET {url}")
    try:
        r = requests.get(
            url,
            headers={"User-Agent": HEADERS["User-Agent"]},
            timeout=15,
            allow_redirects=True,
        )
        print(f"   HTTP {r.status_code}  final url: {r.url}")
        print(f"   content-type: {r.headers.get('content-type')}")
        # El portal redirige a una página de T&C si no aceptaste cookies.
        is_html = "text/html" in (r.headers.get("content-type") or "")
        if is_html and ("Senate Office of Public Records" in r.text or "Terms" in r.text):
            print("   ✓ portal accesible, pero requiere aceptación de T&C + sesión POST")
            print("     para queries reales. Alternativas:")
            print("     - github.com/jeremiak/senate-stock-watcher-data (dump diario)")
            print("     - QuiverQuant.com (API paga)")
            print("     - Scrape custom con session + POST 'aff_legal_alert'")
        else:
            print("   raw preview:", r.text[:300])
    except Exception as e:
        print(f"   error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    print("Discovery — smart money: 13F institutional + Congress STOCK Act.\n")
    section_a_fts_aapl_en_13fs()
    meta = section_b_berkshire_13fs()
    section_c_parsear_13f(meta)
    section_d_house_disclosures()
    section_d_senate_disclosures()
    print("\n=== Cierre ===")
    print("Lo que vimos:")
    print("  - EDGAR indexa 13F y se puede buscar por ticker (FTS).")
    print("  - Cada 13F-HR trae XML de holdings con CUSIP/value/shares.")
    print("  - House publica ZIP anual con PTR (parseable).")
    print("  - Senate no tiene API pública: hay que session+POST o usar mirror.")
    print()
    print("Próximos pasos del diseño:")
    print("  1. Definir cohort de managers (CIKs) — Berkshire, BlackRock, Vanguard, etc.")
    print("  2. CUSIP catalog (CUSIP → ticker, sector) para joinear holdings con tickers.")
    print("  3. Job que pega EDGAR trimestral y persiste en Mongo Smart.13F.")
    print("  4. Aggregaciones por ticker + por manager + diffs Q-on-Q.")
    print("  5. Frontend Renta Variable con vistas: 'Smart Money Heat' + 'Insiders Compra'.")
