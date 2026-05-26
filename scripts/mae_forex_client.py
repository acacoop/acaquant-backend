"""mae_forex_client.py — feed de dólar oficial PARA LA PC DE OFICINA.

Reemplaza la escritura DIRECTA a Mongo de mae_forex.py por un POST autenticado a
la API. Resultado: Atlas se cierra a la IP del Droplet (sin 0.0.0.0/0) y la PC de
oficina solo necesita internet + tokens — NO acceso a Mongo.

Corre EN LA PC DE OFICINA (no en el Droplet: MAE rechaza la IP del Droplet).
Necesita `pip install requests` y estas env vars en la oficina:
  ACAQUANT_API_URL          ej. https://api.acaquant.com
  DOLAR_INGEST_TOKEN        el mismo string que pusiste en el .env del Droplet
  CF_ACCESS_CLIENT_ID       service token de Cloudflare Access (para pasar CF)
  CF_ACCESS_CLIENT_SECRET   idem

INTEGRACIÓN: en tu mae_forex.py actual, donde hoy escribís a Mongo
(`col.insert_*/update_*`), llamá en su lugar a `enviar_a_api(docs)`. Dejá tu
lógica de polling de MAE tal cual — solo cambia a dónde va el dato al final.
"""
from __future__ import annotations

import os
import time

import requests

API = os.environ.get("ACAQUANT_API_URL", "https://api.acaquant.com").rstrip("/")
ENDPOINT = f"{API}/api/ingest/dolar-oficial"
HEADERS = {
    "X-Ingest-Token": os.environ.get("DOLAR_INGEST_TOKEN", ""),
    "CF-Access-Client-Id": os.environ.get("CF_ACCESS_CLIENT_ID", ""),
    "CF-Access-Client-Secret": os.environ.get("CF_ACCESS_CLIENT_SECRET", ""),
    "Content-Type": "application/json",
}


def enviar_a_api(docs: list[dict], reintentos: int = 3) -> bool:
    """POST de los instrumentos MAE a la API (los mismos dicts que hoy escribías a
    DolarOficialLive). Reintenta ante fallo de red. True si quedó persistido."""
    for i in range(reintentos):
        try:
            r = requests.post(ENDPOINT, json={"docs": docs}, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                return True
            print(f"[mae_forex] API {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[mae_forex] error POST (intento {i + 1}/{reintentos}): {e}")
        time.sleep(2)
    return False


def obtener_instrumentos_mae() -> list[dict]:
    """STUB — pegá acá tu lógica actual de mae_forex.py que pollea api.mae.com.ar
    y devuelve la lista de instrumentos (cada uno con ticker/codigoSegmento/
    codigoPlazo/precioUltimo/variacion/...). Devolvé los mismos dicts que hoy
    escribías a Mongo."""
    raise NotImplementedError("integrar el polling de MAE existente de mae_forex.py")


if __name__ == "__main__":
    while True:
        try:
            docs = obtener_instrumentos_mae()
            ok = enviar_a_api(docs)
            print(f"[mae_forex] {'OK' if ok else 'FALLO'} — {len(docs)} instrumentos")
        except Exception as e:
            print(f"[mae_forex] loop error: {e}")
        time.sleep(30)
