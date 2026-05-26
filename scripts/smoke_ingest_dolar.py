"""smoke_ingest_dolar.py — valida el camino de ingesta del dólar SIN romper nada.

Corré esto DESDE TU NOTEBOOK (donde tenés las credenciales) para confirmar que
funciona el camino completo: CF Access (service token) → endpoint → escritura en
Atlas. Manda un doc con ticker de PRUEBA ("SMOKE_TEST"), NO el oficial — así no
contamina el dólar real (la lectura filtra UST$T/M/000 exacto e ignora éste).

Hacé esto ANTES de tocar tu mae_forex.py y ANTES de cerrar Atlas.

Env vars necesarias (las mismas que va a usar el cliente real):
  ACAQUANT_API_URL          (default https://api.acaquant.com)
  DOLAR_INGEST_TOKEN        el mismo string que pusiste en el .env del Droplet
  CF_ACCESS_CLIENT_ID       service token de Cloudflare (Client ID)
  CF_ACCESS_CLIENT_SECRET   service token de Cloudflare (Client Secret)

    python -m scripts.smoke_ingest_dolar
"""
from __future__ import annotations

import os
import sys

import requests

API = os.environ.get("ACAQUANT_API_URL", "https://api.acaquant.com").rstrip("/")
ENDPOINT = f"{API}/api/ingest/dolar-oficial"

# Instrumento de PRUEBA: ticker/segmento/plazo que NO matchean el filtro oficial
# (UST$T / M / 000) → upsert crea un doc aparte que el dólar real nunca lee.
PRUEBA = {
    "docs": [{
        "ticker": "SMOKE_TEST",
        "codigoSegmento": "TEST",
        "codigoPlazo": "TEST",
        "precioUltimo": 1.0,
        "variacion": 0.0,
    }]
}


def main() -> int:
    tok = os.environ.get("DOLAR_INGEST_TOKEN", "")
    cid = os.environ.get("CF_ACCESS_CLIENT_ID", "")
    csec = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")
    faltan = [n for n, v in [("DOLAR_INGEST_TOKEN", tok),
                             ("CF_ACCESS_CLIENT_ID", cid),
                             ("CF_ACCESS_CLIENT_SECRET", csec)] if not v]
    if faltan:
        print(f"❌ Faltan env vars: {', '.join(faltan)}")
        return 1

    headers = {
        "X-Ingest-Token": tok,
        "CF-Access-Client-Id": cid,
        "CF-Access-Client-Secret": csec,
        "Content-Type": "application/json",
    }
    print(f"POST {ENDPOINT}")
    try:
        r = requests.post(ENDPOINT, json=PRUEBA, headers=headers, timeout=15)
    except Exception as e:
        print(f"❌ No se pudo conectar: {e}")
        return 1

    ct = r.headers.get("content-type", "")
    if r.status_code == 200 and "json" in ct:
        print(f"✅ OK — el camino completo funciona. Respuesta: {r.text}")
        print("   (El doc SMOKE_TEST es inofensivo: no lo lee el dólar oficial.)")
        print("   Ya podés migrar mae_forex.py y, cuando veas el dólar real entrar, cerrar Atlas.")
        return 0

    # Diagnóstico por tipo de fallo
    print(f"❌ Falló — HTTP {r.status_code}")
    if "text/html" in ct or "<html" in r.text[:200].lower():
        print("   → Te devolvió HTML (login de Cloudflare). CF Access NO dejó pasar el service token.")
        print("     Revisá: la policy de la app api.acaquant.com debe ser Action=Service Auth con tu token.")
    elif r.status_code == 401:
        print("   → 401: X-Ingest-Token no coincide con el DOLAR_INGEST_TOKEN del .env del Droplet.")
    elif r.status_code == 503:
        print("   → 503: el Droplet no tiene DOLAR_INGEST_TOKEN seteado (o no reiniciaste api.service).")
    else:
        print(f"   → Respuesta: {r.text[:300]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
