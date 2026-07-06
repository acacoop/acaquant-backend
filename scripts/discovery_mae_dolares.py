"""discovery_mae_dolares.py — QUÉ DÓLARES EXPONE EL FEED MAE (read-only).

CORRE EN LA NOTEBOOK DE LA OFICINA (la misma que corre `mae_forex.py`), NO en el
Droplet — MAE rechaza la IP del Droplet. Solo necesita `requests`:

    pip install requests
    python scripts\\discovery_mae_dolares.py

Para qué sirve
──────────────
Hoy el feed del dólar oficial manda UN SOLO instrumento: UST$T / M / 000 (dólar
mayorista A3500). Queremos sumar BANCO NACIÓN COMPRADOR, DÓLAR BNA DIVISA y DÓLAR
MATBA ROFEX — pero **no sabemos con qué ticker / codigoSegmento / codigoPlazo los
publica MAE** (ni si los publica en este endpoint). Este script NO asume nada:
pega al feed, vuelca TODOS los instrumentos que devuelve y marca los candidatos
que matchean "nacion / bna / matba / rofex / divisa / minorista" en cualquiera de
sus campos. Con esa lista salen los códigos exactos para reformular el feed.

Es 100% de lectura: no escribe a la API ni a ninguna base. Solo imprime y (opcional)
deja un JSON con el volcado crudo para revisar/pegar.

Config
──────
Completá los 2 valores de abajo con lo mismo que ya usás en tu `mae_forex.py`.
Si el dólar oficial actual sale de un endpoint y sospechás que estos otros dólares
salen de otro, agregá esa URL a EXTRA_URLS y el script la barre también.
"""
from __future__ import annotations

import json
import sys

import requests

# ════════════════════════════════════════════════════════════════════════════
# CONFIG — completá con lo mismo de tu mae_forex.py
# ════════════════════════════════════════════════════════════════════════════

MAE_API_KEY = "PEGAR_API_KEY_MAE"
MAE_FOREX_URL = "PEGAR_URL_FOREX_MAE"   # la URL de la que hoy sacás UST$T

# URLs extra a barrer, por si estos dólares viven en otro endpoint MAE.
# Ejemplos posibles (descomentá / ajustá si aplica) — el script tolera 404.
EXTRA_URLS: list[str] = [
    # "https://api.mae.com.ar/api/v1/mercado/cotizaciones/divisas",
    # "https://api.mae.com.ar/api/v1/mercado/cotizaciones/minorista",
]

# Cuántas páginas pedir por URL (el feed pagina con pageNumber). 3 suele alcanzar.
MAX_PAGES = 3

# Si True, además de imprimir, deja el volcado crudo en este archivo (para pegar).
DUMP_JSON = True
DUMP_PATH = "mae_dolares_discovery.json"

# ════════════════════════════════════════════════════════════════════════════

# Palabras que delatan a los dólares que buscamos (case-insensitive), buscadas en
# TODOS los valores de cada instrumento — así aparece aunque el nombre esté en un
# campo que no conocemos.
CANDIDATOS = ("nacion", "nación", "bna", "matba", "rofex", "divisa", "minorista",
              "comprador", "vendedor", "banco")


def _fetch(url: str, api_key: str) -> list[dict]:
    """Pega a `url` con x-api-key, recorre hasta MAX_PAGES y junta los items.
    Devuelve [] ante cualquier fallo (imprime el motivo)."""
    out: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        try:
            r = requests.get(
                url,
                headers={"x-api-key": api_key},
                params={"pageNumber": page},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"   [pag {page}] error: {e}")
            break
        if not isinstance(data, list):
            print(f"   [pag {page}] respuesta no-lista ({type(data).__name__}) — "
                  f"muestro tal cual:")
            print("   " + json.dumps(data, ensure_ascii=False)[:800])
            break
        if not data:
            break
        out.extend(it for it in data if isinstance(it, dict))
        if len(data) < 2:  # última página casi vacía → cortar
            break
    return out


def _es_candidato(item: dict) -> bool:
    blob = json.dumps(item, ensure_ascii=False).lower()
    return any(w in blob for w in CANDIDATOS)


def _fila(item: dict) -> str:
    return (f"   ticker={item.get('ticker')!r:12} "
            f"seg={item.get('codigoSegmento')!r:5} "
            f"plazo={item.get('codigoPlazo')!r:6} "
            f"ult={item.get('precioUltimo')!r:>12} "
            f"var={item.get('variacion')!r}")


def main() -> None:
    if "PEGAR_" in MAE_API_KEY or "PEGAR_" in MAE_FOREX_URL:
        print("⛔ Faltan credenciales. Editá MAE_API_KEY y MAE_FOREX_URL arriba.")
        sys.exit(1)

    urls = [MAE_FOREX_URL, *EXTRA_URLS]
    todos: list[dict] = []

    for url in urls:
        print(f"\n=== {url} ===")
        items = _fetch(url, MAE_API_KEY)
        print(f"   → {len(items)} instrumentos")
        todos.extend(items)

    if not todos:
        print("\nSin instrumentos. Revisá URL/API key (o probá EXTRA_URLS).")
        return

    # 1) Qué campos trae cada instrumento (para saber dónde vive el nombre).
    keys: set[str] = set()
    for it in todos:
        keys.update(it.keys())
    print(f"\n── CAMPOS PRESENTES ({len(keys)}) ──")
    print("   " + ", ".join(sorted(keys)))

    # 2) Todos los ticker/segmento/plazo distintos (el universo completo).
    print(f"\n── UNIVERSO COMPLETO ({len(todos)} instrumentos) ──")
    vistos = set()
    for it in sorted(todos, key=lambda x: (str(x.get("ticker")),
                                           str(x.get("codigoSegmento")),
                                           str(x.get("codigoPlazo")))):
        clave = (it.get("ticker"), it.get("codigoSegmento"), it.get("codigoPlazo"))
        if clave in vistos:
            continue
        vistos.add(clave)
        print(_fila(it))

    # 3) Candidatos a los dólares que buscamos (match por palabra clave).
    cands = [it for it in todos if _es_candidato(it)]
    print(f"\n── CANDIDATOS (nacion/bna/matba/rofex/divisa/minorista…) — {len(cands)} ──")
    if not cands:
        print("   Ninguno matcheó en este/estos endpoint(s). Probable que estos "
              "dólares NO vengan de MAE forex → hay que buscar otra fuente "
              "(otro endpoint MAE en EXTRA_URLS, o BNA/Matba directo).")
    for it in cands:
        print(_fila(it))
        # Volcado completo del candidato — así vemos el nombre/descripción real.
        print("      " + json.dumps(it, ensure_ascii=False))

    if DUMP_JSON:
        try:
            with open(DUMP_PATH, "w", encoding="utf-8") as f:
                json.dump(todos, f, ensure_ascii=False, indent=2)
            print(f"\n📄 Volcado crudo completo → {DUMP_PATH} "
                  f"(mandámelo o pegá los candidatos).")
        except Exception as e:
            print(f"\n(no se pudo escribir {DUMP_PATH}: {e})")


if __name__ == "__main__":
    main()
