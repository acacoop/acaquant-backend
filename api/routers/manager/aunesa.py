"""Manager · Aunesa — exploradores de los endpoints de Aunesa.

Hoy:
  GET /api/manager/aunesa/explorar?fecha=YYYY-MM-DD
    → pega a /operaciones/consolidadosGenerales y devuelve un JSON con
      todos los movimientos del día + análisis del filtro actual de
      jobs/cashflow.py (capturado vs descartado por palabras clave).

Útil para discovery: ver qué tipos de movimientos vienen y qué se está
descartando hoy. Sirve como base para extender la captura más allá de
deposito/transferencia/extraccion.
"""
from __future__ import annotations

import logging
import unicodedata
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Query

import config

router = APIRouter()
logger = logging.getLogger("api.manager.aunesa")

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
OPS_URL = "https://aca.aunesa.com/Irmo/api/operaciones/consolidadosGenerales"

# Mismo filtro que jobs/cashflow.py — para reportar qué descartamos.
PALABRAS_CLAVE_ACTUALES = ("deposito", "transferencia", "extraccion")

# Movimientos que se excluyen ANTES del análisis (no nos interesan en absoluto).
# Si `informacion` o `cuenta` contiene cualquiera de estas substrings (case-
# insensitive, sin acentos), el movimiento se omite del response.
EXCLUIR_SUBSTRINGS = ("otc",)


def _autenticar() -> dict[str, str]:
    resp = requests.post(
        AUTH_URL,
        json={
            "clientId": config.AUNESA_CLIENT_ID,
            "username": config.AUNESA_USERNAME,
            "password": config.AUNESA_PASSWORD,
        },
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def _normalizar(s: str) -> str:
    return (
        unicodedata.normalize("NFD", str(s))
        .encode("ascii", "ignore")
        .decode("utf-8")
        .lower()
    )


def _es_capturado(informacion: str) -> bool:
    norm = _normalizar(informacion)
    return any(p in norm for p in PALABRAS_CLAVE_ACTUALES)


def _excluir(mov: dict) -> bool:
    """True si el movimiento debe descartarse antes del análisis (ej. OTC)."""
    info_norm = _normalizar(mov.get("informacion") or "")
    cuenta_norm = _normalizar(mov.get("cuenta") or "")
    return any(s in info_norm or s in cuenta_norm for s in EXCLUIR_SUBSTRINGS)


@router.get("/aunesa/explorar")
def aunesa_explorar(
    fecha: str | None = Query(None, description="YYYY-MM-DD; default: hoy ART"),
    tipos_cuenta: str = Query(
        "Comitente",
        description="tipoCuenta param de Aunesa (default Comitente, igual que el cron actual)",
    ),
) -> dict[str, Any]:
    """Discovery del endpoint /operaciones/consolidadosGenerales.

    Devuelve:
      - meta: fecha pedida, tiposCuenta, total, capturados, descartados.
      - tipos: lista de tipos distintos de `informacion` con count y flag
        capturado.
      - movimientos: TODOS los movimientos del día con un campo
        `_capturado` agregado para que el front pueda filtrar.
      - keys_universo: union de keys presentes en cualquier movimiento
        (útil para construir filtros dinámicos en el front).

    Si la fecha pedida no tiene movimientos, devuelve total=0 sin error.
    """
    if fecha:
        try:
            d = datetime.strptime(fecha, "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(status_code=400,
                                detail=f"fecha mal formada: {fecha} (esperado YYYY-MM-DD)") from e
    else:
        d = (datetime.now(UTC) - timedelta(hours=3)).date()

    dia_str = d.strftime("%d/%m/%Y")

    # Auth + GET con retries por si Aunesa está lento.
    try:
        headers = _autenticar()
    except Exception as e:
        logger.exception("aunesa explorar auth failed")
        raise HTTPException(status_code=502,
                            detail=f"falla de auth con Aunesa: {e}") from e

    params = {
        "tiposCuenta":      tipos_cuenta,
        "concertacionDesde": dia_str,
        "concertacionHasta": dia_str,
    }
    resp = None
    last_err: Exception | None = None
    for intento in range(1, 4):
        try:
            resp = requests.get(OPS_URL, params=params, headers=headers, timeout=180)
            break
        except requests.exceptions.Timeout as e:
            last_err = e
            logger.warning("timeout aunesa explorar intento %d/3", intento)
            continue
        except Exception as e:
            last_err = e
            break
    if resp is None:
        raise HTTPException(status_code=504,
                            detail=f"timeout tras 3 intentos: {last_err}")
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code,
                            detail=f"Aunesa: {resp.text[:300]}")

    data = resp.json()
    if not isinstance(data, list):
        raise HTTPException(status_code=502,
                            detail=f"Aunesa devolvió shape inesperado: {type(data).__name__}")

    # Filtro pre-análisis: excluir movimientos no deseados (OTC, etc).
    raw_total = len(data)
    data = [r for r in data if not _excluir(r)]
    excluidos_n = raw_total - len(data)

    # Análisis.
    total = len(data)
    counter: Counter[str] = Counter()
    capturados_n = 0
    descartados_n = 0
    keys_universo: set[str] = set()
    movimientos: list[dict[str, Any]] = []

    for r in data:
        informacion = r.get("informacion") or "(sin informacion)"
        capturado = _es_capturado(informacion)
        if capturado:
            capturados_n += 1
        else:
            descartados_n += 1
        counter[informacion] += 1
        keys_universo.update(r.keys())

        # Inyectamos el flag en cada doc para que el front pueda filtrar
        # sin re-evaluar las palabras clave.
        mov = dict(r)
        mov["_capturado"] = capturado
        movimientos.append(mov)

    tipos = [
        {"informacion": tipo, "count": n, "capturado": _es_capturado(tipo)}
        for tipo, n in counter.most_common()
    ]

    pct_cap = round((capturados_n / total) * 100, 1) if total else 0.0
    pct_desc = round((descartados_n / total) * 100, 1) if total else 0.0

    return {
        "meta": {
            "fecha":         d.isoformat(),
            "tiposCuenta":   tipos_cuenta,
            "total":         total,
            "capturados":    capturados_n,
            "descartados":   descartados_n,
            "pct_capturados": pct_cap,
            "pct_descartados": pct_desc,
            "palabras_clave_actuales": list(PALABRAS_CLAVE_ACTUALES),
            "excluir_substrings": list(EXCLUIR_SUBSTRINGS),
            "raw_total": raw_total,
            "excluidos": excluidos_n,
        },
        "tipos":         tipos,
        "movimientos":   movimientos,
        "keys_universo": sorted(keys_universo),
    }
