"""mae_forex_client.py — feed de dólar oficial PARA TU NOTEBOOK (Anaconda).

Hace lo mismo que tu mae_forex.py original (pollea MAE cada 30s) pero, en vez de
escribir DIRECTO a Mongo, manda el dólar oficial a la API
(POST /api/ingest/dolar-oficial). El Droplet (IP whitelisteada en Atlas) lo
persiste. Resultado: cerrás el 0.0.0.0/0 de Atlas.

CORRE EN TU NOTEBOOK, no en el Droplet (MAE rechaza la IP del Droplet).
    pip install requests
    python scripts\\mae_forex_client.py

Todo se configura en el bloque CONFIG de abajo — completá los 6 valores.

A diferencia del script viejo, manda SOLO el dólar oficial mayorista
(UST$T / M / 000), que es el único que el backend lee. Así DolarOficialLive
no se ensucia con instrumentos que nadie usa.

OJO seguridad: este archivo está en el repo. NO commitees la versión con las
keys reales pegadas (dejá los placeholders en git, pegá los valores solo en tu
copia local).
"""
from __future__ import annotations

import time

import requests

# ════════════════════════════════════════════════════════════════════════════
# CONFIG — completá estos valores en tu notebook
# ════════════════════════════════════════════════════════════════════════════

# --- MAE (lo mismo que tenías en mae_forex.py) ---
MAE_API_KEY = "PEGAR_API_KEY_MAE"
MAE_FOREX_URL = "PEGAR_URL_FOREX_MAE"

# --- API acaquant (destino del POST) ---
ACAQUANT_API_URL = "https://api.acaquant.com"
DOLAR_INGEST_TOKEN = "PEGAR_DOLAR_INGEST_TOKEN"   # el mismo del .env del Droplet
CF_ACCESS_CLIENT_ID = "PEGAR_CF_CLIENT_ID"        # service token Cloudflare Access
CF_ACCESS_CLIENT_SECRET = "PEGAR_CF_CLIENT_SECRET"

# --- Interruptores de prueba ---
DRY_RUN = True    # True  = pollea MAE e IMPRIME lo que mandaría, NO envía nada.
                  # False = envía de verdad a la API.
RUN_ONCE = True   # True  = una sola pasada y corta. False = loop infinito cada 30s.

INTERVALO_SEG = 30

# ════════════════════════════════════════════════════════════════════════════

ENDPOINT = f"{ACAQUANT_API_URL.rstrip('/')}/api/ingest/dolar-oficial"
HEADERS = {
    # .strip() defensivo: al copiar credenciales del dashboard se cuelan
    # espacios/saltos de línea y requests rechaza headers con whitespace.
    "X-Ingest-Token": DOLAR_INGEST_TOKEN.strip(),
    "CF-Access-Client-Id": CF_ACCESS_CLIENT_ID.strip(),
    "CF-Access-Client-Secret": CF_ACCESS_CLIENT_SECRET.strip(),
    "Content-Type": "application/json",
}


def fetch_mae() -> list[dict]:
    """Pollea MAE (GET con x-api-key + pageNumber=1) y devuelve SOLO el dólar
    oficial mayorista: UST$T / M / 000. Es el único instrumento que el backend
    lee, así que mandar el resto solo ensuciaba DolarOficialLive."""
    r = requests.get(
        MAE_FOREX_URL,
        headers={"x-api-key": MAE_API_KEY},
        params={"pageNumber": 1},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        print(f"[mae_forex] respuesta inesperada (no es lista): {type(data).__name__}")
        return []
    return [
        it for it in data
        if isinstance(it, dict)
        and it.get("ticker") == "UST$T"
        and it.get("codigoSegmento") == "M"
        and it.get("codigoPlazo") == "000"
    ]


def enviar_a_api(docs: list[dict], reintentos: int = 3) -> bool:
    """POST del dólar oficial a la API. Reintenta ante fallo de red.
    True solo si quedó persistido (200 + JSON)."""
    for i in range(reintentos):
        try:
            r = requests.post(ENDPOINT, json={"docs": docs}, headers=HEADERS, timeout=10)
            ct = r.headers.get("content-type", "")
            # Éxito SOLO si es 200 + JSON. CF Access devuelve el login HTML con
            # 200 cuando el service token no es aceptado → NO es éxito.
            if r.status_code == 200 and "json" in ct:
                print(f"[mae_forex] API OK: {r.text}")
                return True
            if "text/html" in ct or "<html" in r.text[:200].lower():
                print("[mae_forex] CF Access devolvió el login HTML — el service token NO "
                      "fue aceptado. Revisá la policy 'Service Auth' de la app api.acaquant.com.")
            else:
                print(f"[mae_forex] API {r.status_code}: {r.text[:300]}")
        except Exception as e:
            print(f"[mae_forex] error POST (intento {i + 1}/{reintentos}): {e}")
        time.sleep(2)
    return False


def una_pasada() -> None:
    """Una iteración: pollea MAE y (salvo DRY_RUN) manda a la API."""
    docs = fetch_mae()
    if not docs:
        print("[mae_forex] sin datos (MAE no devolvió UST$T/M/000 en este poll)")
        return
    if DRY_RUN:
        print(f"[mae_forex] DRY_RUN — {len(docs)} instrumento(s) (NO se envía nada):")
        for it in docs:
            print(f"   ticker={it.get('ticker')!r:8} seg={it.get('codigoSegmento')!r:4} "
                  f"plazo={it.get('codigoPlazo')!r:6} ult={it.get('precioUltimo')!r:>10} "
                  f"var={it.get('variacion')!r}")
        return
    ok = enviar_a_api(docs)
    print(f"[mae_forex] {'OK' if ok else 'FALLO'} — {len(docs)} instrumento(s)")


def main() -> None:
    modo = "DRY_RUN (no envía)" if DRY_RUN else "ENVÍO REAL"
    bucle = "una pasada" if RUN_ONCE else f"loop cada {INTERVALO_SEG}s"
    print(f"[mae_forex] arranque — {modo}, {bucle}")
    while True:
        try:
            una_pasada()
        except Exception as e:
            print(f"[mae_forex] loop error: {e}")
        if RUN_ONCE:
            break
        time.sleep(INTERVALO_SEG)


if __name__ == "__main__":
    main()
