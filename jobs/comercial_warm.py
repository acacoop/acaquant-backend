"""jobs/comercial_warm.py — precalienta la cache in-process de la vista COMERCIAL.

La cache (`@cached`) vive en el proceso de la API (uvicorn). Un cron común NO
la calienta porque corre en OTRO proceso (misma razón por la que workers
romperían la cache). Por eso este job pega por HTTP al **uvicorn local**
(127.0.0.1:8000) — así la cache se popula DENTRO del proceso de la API.

Para cada operador llama operador_comercial + serie(volumen) + serie(aum), que
es lo que pide la vista al abrirla. Pensado para correr cada ~4-5 min en
horario de mercado (TTL de la cache = 300s) → los operadores la agarran caliente.

Read-only (solo GET). Uso: python -m jobs.comercial_warm
"""
from __future__ import annotations

import logging
import os
import time
from urllib.parse import quote

import requests

from config import API_KEY

BASE = os.getenv("API_LOCAL_URL", "http://127.0.0.1:8000")
# RBAC: un admin (de MANAGER_EMAILS) pasa el gate del módulo `operaciones`.
ADMIN_EMAIL = (os.getenv("MANAGER_EMAILS", "").split(",")[0] or "").strip()


def _headers() -> dict[str, str]:
    h = {"x-acaquant-user-email": ADMIN_EMAIL}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("comercial_warm")
    if not ADMIN_EMAIL:
        log.warning("MANAGER_EMAILS vacío — sin email admin, el RBAC puede rechazar. Abortando.")
        return

    h = _headers()
    t0 = time.perf_counter()
    try:
        r = requests.get(f"{BASE}/api/operaciones/comercial/operadores", headers=h, timeout=30)
        r.raise_for_status()
        ops = r.json()
    except Exception as e:
        log.error("no se pudo listar operadores (¿API local arriba?): %s", e)
        return

    hechos = 0
    for o in ops:
        em = quote(o.get("operador_email", ""))
        if not em:
            continue
        for url in (
            f"{BASE}/api/operaciones/comercial/operador?operador={em}",
            f"{BASE}/api/operaciones/comercial/serie?operador={em}&metric=volumen",
            f"{BASE}/api/operaciones/comercial/serie?operador={em}&metric=aum",
        ):
            try:
                requests.get(url, headers=h, timeout=30).raise_for_status()
                hechos += 1
            except Exception as e:
                log.warning("warm falló %s: %s", url, e)

    log.info(
        "COMERCIAL precalentado: %d operadores · %d requests · %.1fs",
        len(ops), hechos, time.perf_counter() - t0,
    )


if __name__ == "__main__":
    from core.job_runs import JobRunLogger
    with JobRunLogger("comercial_warm"):
        main()
