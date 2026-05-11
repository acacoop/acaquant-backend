"""Probe de la SEC EDGAR API usando Apple (CIK 320193) como conejillo.

Objetivo: validar end-to-end antes de comprometernos a usar SEC EDGAR como
source de filings para los ADRs argentinos. Chequea:

  A. Endpoint de submissions: trae la lista completa de filings históricos
     de la empresa. Muestra qué fields trae y los 20 más recientes.
  B. Cuenta cuántos filings hay por form-type (10-K, 10-Q, 8-K, etc.) —
     útil para entender qué cobertura tenemos.
  C. Toma el filing más reciente, arma la URL al documento HTML real, y
     muestra los primeros KB del contenido para confirmar que se puede
     descargar.

Rate limit SEC: 10 req/s con User-Agent válido (acá usamos 3 requests max).
Auth: ninguna. Solo el header User-Agent es obligatorio.

Uso:
    python -m scripts.probe_sec_edgar
"""
from __future__ import annotations

import json
from collections import Counter

import requests

# SEC requiere un User-Agent identificable. Si lo dejás genérico te bloquean.
HEADERS = {
    "User-Agent": "TradingAV Research mollonicolas95@gmail.com",
    "Accept": "application/json",
}

APPLE_CIK = "0000320193"  # 10 dígitos zero-padded, formato que pide SEC


def section_a_submissions(cik: str) -> dict | None:
    print("=== A. /submissions/CIK{cik}.json — lista completa de filings ===")
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    print(f"   GET {url}")
    print(f"   headers: {HEADERS}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        print(f"   HTTP {r.status_code}")
        print(f"   content-type: {r.headers.get('content-type')}")
        if r.status_code != 200:
            print(f"   body: {r.text[:400]}")
            return None
        j = r.json()
        # Top-level metadata interesante.
        print("\n   --- Metadata de la empresa ---")
        print(f"   name:    {j.get('name')}")
        print(f"   tickers: {j.get('tickers')}")
        print(f"   exchanges: {j.get('exchanges')}")
        print(f"   sic:     {j.get('sic')} ({j.get('sicDescription')})")
        print(f"   formerNames: {[x.get('name') for x in (j.get('formerNames') or [])]}")
        print(f"   ein:     {j.get('ein')}")

        filings = (j.get("filings") or {}).get("recent") or {}
        if not filings.get("accessionNumber"):
            print("   ⚠ Sin filings recientes en el response (rare)")
            return j

        n = len(filings["accessionNumber"])
        print(f"\n   --- Filings 'recent' bucket: {n} totales ---")
        keys = list(filings.keys())
        print(f"   fields disponibles por filing: {keys}")

        # Mostramos los 20 más recientes con los campos clave.
        print("\n   --- 20 más recientes ---")
        print(
            f"   {'#':>3} {'FECHA':<11} {'FORM':<10} {'ACCESSION':<22} "
            f"{'PRIMARY DOC':<40}"
        )
        for i in range(min(20, n)):
            print(
                f"   {i:>3} "
                f"{filings['filingDate'][i]:<11} "
                f"{filings['form'][i]:<10} "
                f"{filings['accessionNumber'][i]:<22} "
                f"{(filings['primaryDocument'][i] or '')[:40]:<40}"
            )
        return j
    except Exception as e:
        print(f"   error: {e}")
        return None


def section_b_form_breakdown(j: dict | None) -> None:
    print("\n=== B. Distribución por tipo de formulario (bucket recent) ===")
    if not j:
        print("   sin data")
        return
    forms = ((j.get("filings") or {}).get("recent") or {}).get("form") or []
    if not forms:
        print("   sin filings")
        return
    c = Counter(forms)
    print(f"   {len(forms)} filings totales en el bucket")
    for form, count in c.most_common(15):
        print(f"     {form:<12} {count:>4}")


def section_c_descargar_filing(j: dict | None) -> None:
    print("\n=== C. Descargar el filing más reciente (primer KB del HTML) ===")
    if not j:
        print("   sin data")
        return
    filings = (j.get("filings") or {}).get("recent") or {}
    if not filings.get("accessionNumber"):
        print("   sin filings")
        return

    cik = str(j.get("cik") or "").lstrip("0") or "320193"
    # El accession number viene como "0000320193-25-000123"; SEC lo quiere
    # SIN guiones en el path del Archives. El nombre del documento viene
    # en `primaryDocument`.
    acc = filings["accessionNumber"][0]
    acc_clean = acc.replace("-", "")
    doc = filings["primaryDocument"][0]
    form = filings["form"][0]
    fecha = filings["filingDate"][0]

    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{doc}"
    print(f"   filing más reciente: form={form} fecha={fecha}")
    print(f"   accession: {acc}")
    print(f"   GET {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        print(f"   HTTP {r.status_code}")
        print(f"   content-type: {r.headers.get('content-type')}")
        print(f"   content-length: {r.headers.get('content-length')}")
        if r.status_code != 200:
            print(f"   body: {r.text[:300]}")
            return
        # Mostramos solo los primeros 800 chars para no inundar la consola.
        preview = r.text[:800].replace("\n", " ")
        print("\n   --- Primeros 800 chars del HTML ---")
        print(f"   {preview}")
        print(f"\n   (... documento completo: {len(r.text):,} chars)")
    except Exception as e:
        print(f"   error: {e}")


def section_d_xbrl(cik: str) -> None:
    """Bonus: prueba el endpoint XBRL companyfacts — financials estructurados.
    Es el que nos permitiría armar series de Revenue/NetIncome/Assets/etc.
    sin parsear HTML.
    """
    print("\n=== D. /api/xbrl/companyfacts/CIK{cik}.json — XBRL structured (opcional) ===")
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    print(f"   GET {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        print(f"   HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"   body: {r.text[:300]}")
            return
        j = r.json()
        facts = j.get("facts") or {}
        print(f"   entity: {j.get('entityName')}")
        print(f"   taxonomies disponibles: {list(facts.keys())}")
        # Sample: revenue de los últimos 5 reportes anuales.
        us_gaap = facts.get("us-gaap") or {}
        if "Revenues" in us_gaap:
            r_unit = us_gaap["Revenues"].get("units") or {}
            usd = r_unit.get("USD") or []
            anuales = [x for x in usd if x.get("form") == "10-K"]
            print("\n   --- Revenue (us-gaap:Revenues, USD, form=10-K) últimos 5 ---")
            for r_doc in sorted(anuales, key=lambda x: x.get("end", ""), reverse=True)[:5]:
                print(
                    f"     end={r_doc.get('end')}  "
                    f"fy={r_doc.get('fy')}  "
                    f"val=${r_doc.get('val'):,.0f}  "
                    f"form={r_doc.get('form')}"
                )
        else:
            # Apple a veces reporta como RevenueFromContractWithCustomerExcludingAssessedTax
            print("   (Revenues no encontrado, alguna empresa reporta con otro tag)")
            sample_tags = list(us_gaap.keys())[:15]
            print(f"   sample tags us-gaap disponibles: {sample_tags}")
    except json.JSONDecodeError as e:
        print(f"   error parseando JSON: {e}")
    except Exception as e:
        print(f"   error: {e}")


if __name__ == "__main__":
    print("Probe SEC EDGAR — usando Apple (CIK 320193) como sample.\n")
    j = section_a_submissions(APPLE_CIK)
    section_b_form_breakdown(j)
    section_c_descargar_filing(j)
    section_d_xbrl(APPLE_CIK)
    print("\n=== Cierre ===")
    print("Si las 4 secciones devolvieron 200 OK → SEC EDGAR es viable.")
    print("Próximo paso: armar el job que pega para los CIK de los ADRs argentinos.")
