"""core/atlas_api.py — lector de la Atlas Admin API (REST de gestión).

Lee CPU del cluster y slow queries del M10 SIN agregar carga de queries a la DB:
cloud.mongodb.com es una API REST de gestión, separada del cluster — no le manda
queries al M10. Base para el watchdog de DB (alertas) y un informe de salud.

Auth: HTTP Digest con las MISMAS env vars que deploy/atlas_cluster.sh:
  ATLAS_PUBLIC_KEY, ATLAS_PRIVATE_KEY, ATLAS_PROJECT_ID, ATLAS_CLUSTER_NAME

core/ no importa nada del proyecto (regla de capas) → solo os + requests.
"""
from __future__ import annotations

import json
import os
from typing import Any

import requests
from requests.auth import HTTPDigestAuth

_BASE = "https://cloud.mongodb.com/api/atlas/v2"
# La API v2 exige versionar por Accept header. Fecha estable de la versión.
_ACCEPT = "application/vnd.atlas.2023-11-15+json"
_TIMEOUT = 30


def _cfg() -> tuple[str, str, str]:
    # Preferimos la key READ-ONLY dedicada (ATLAS_RO_*) por mínimo privilegio; si
    # no está, caemos a la de gestión (ATLAS_*). Así el watchdog usa una key que
    # SOLO lee, sin tocar atlas_cluster.sh (que sigue con ATLAS_*, que pausa/prende).
    pub = (os.getenv("ATLAS_RO_PUBLIC_KEY") or os.getenv("ATLAS_PUBLIC_KEY") or "").strip()
    priv = (os.getenv("ATLAS_RO_PRIVATE_KEY") or os.getenv("ATLAS_PRIVATE_KEY") or "").strip()
    proj = (os.getenv("ATLAS_PROJECT_ID") or "").strip()
    if not (pub and priv and proj):
        raise RuntimeError(
            "Faltan env vars de Atlas: (ATLAS_RO_PUBLIC_KEY/ATLAS_RO_PRIVATE_KEY o "
            "ATLAS_PUBLIC_KEY/ATLAS_PRIVATE_KEY) + ATLAS_PROJECT_ID."
        )
    return pub, priv, proj


def get(path: str, params: dict | None = None) -> dict[str, Any]:
    """GET contra la Atlas Admin API (relativo a /groups/{PROJECT_ID}). Digest auth."""
    pub, priv, proj = _cfg()
    url = f"{_BASE}/groups/{proj}{path}"
    r = requests.get(url, auth=HTTPDigestAuth(pub, priv),
                     headers={"Accept": _ACCEPT}, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def processes() -> list[dict]:
    """Nodos del proyecto: [{id: 'host:port', typeName, userAlias, ...}]."""
    return get("/processes").get("results", [])


def _cluster_name() -> str:
    name = (os.getenv("ATLAS_CLUSTER_NAME") or "").strip()
    if not name:
        raise RuntimeError("Falta ATLAS_CLUSTER_NAME en el entorno.")
    return name


def suggested_indexes(process_id: str) -> dict[str, Any]:
    """Performance Advisor: índices que Atlas sugiere CREAR para el nodo, según el
    shape de las queries lentas. Solo metadatos estructurales (namespace, campos),
    sin valores de datos → se puede mostrar tal cual."""
    return get(f"/processes/{process_id}/performanceAdvisor/suggestedIndexes")


def drop_index_suggestions() -> dict[str, Any]:
    """Performance Advisor: índices que Atlas sugiere DROPEAR a nivel cluster —
    ocultos / redundantes / sin uso. Es el análisis que Atlas computa solo, sin
    necesidad de $indexStats. Solo metadatos estructurales, sin valores."""
    return get(f"/clusters/{_cluster_name()}/performanceAdvisor/dropIndexSuggestions")


def _ultimo_valor(measurements: list[dict], nombre: str) -> float | None:
    """Último dataPoint NO nulo de la métrica `nombre`."""
    for m in measurements:
        if m.get("name") != nombre:
            continue
        for dp in reversed(m.get("dataPoints") or []):
            v = dp.get("value")
            if v is not None:
                return float(v)
    return None


def cpu_por_nodo(period: str = "PT10M", granularity: str = "PT1M") -> list[dict]:
    """% CPU normalizado (user+kernel) reciente por nodo. Devuelve
    [{id, alias, tipo, cpu_pct}]. cpu_pct None si no hay dato."""
    out: list[dict] = []
    for p in processes():
        pid = p.get("id")
        if not pid:
            continue
        data = get(f"/processes/{pid}/measurements",
                   {"granularity": granularity, "period": period,
                    "m": ["SYSTEM_NORMALIZED_CPU_USER", "SYSTEM_NORMALIZED_CPU_KERNEL"]})
        meas = data.get("measurements") or []
        user = _ultimo_valor(meas, "SYSTEM_NORMALIZED_CPU_USER")
        kern = _ultimo_valor(meas, "SYSTEM_NORMALIZED_CPU_KERNEL")
        cpu = None if user is None and kern is None else round((user or 0) + (kern or 0), 1)
        out.append({"id": pid, "alias": p.get("userAlias") or pid,
                    "tipo": p.get("typeName"), "cpu_pct": cpu})
    return out


def _slow_queries_raw(process_id: str, since_ms: int | None, n_logs: int) -> list[dict]:
    """Respuesta cruda del Performance Advisor. PRIVADA: el crudo puede contener
    el comando con VALORES (montos, nombres). NUNCA exponerlo fuera de este módulo
    ni mandarlo a logs/Telegram. Solo lo consume `slow_queries_meta` para redactar."""
    params: dict[str, Any] = {"nLogs": n_logs}
    if since_ms is not None:
        params["since"] = since_ms
    data = get(f"/processes/{process_id}/performanceAdvisor/slowQueryLogs", params)
    return data.get("slowQueries") or []


# Whitelist de campos SEGUROS de un slow query log: metadatos, sin valores de datos.
# Todo lo que no esté acá (command, q, u, filter, originatingCommand, el doc, etc.)
# se DESCARTA — garantía de que el monitoreo nunca filtra data financiera.
_SAFE_KEYS = (
    "ns", "planSummary", "docsExamined", "keysExamined", "nreturned",
    "nReturned", "durationMillis", "millis", "queryHash", "appName", "op",
)


def slow_queries_meta(process_id: str, since_ms: int | None = None,
                      n_logs: int = 200) -> list[dict]:
    """Slow queries REDACTADAS: solo metadatos (ns, planSummary, docsExamined, etc.),
    NUNCA el comando ni valores. Es la ÚNICA interfaz pública de slow queries — el
    crudo no sale de este módulo. `appName` se incluye para atribuir (qué app/job)."""
    out: list[dict] = []
    for item in _slow_queries_raw(process_id, since_ms, n_logs):
        line = item.get("line")
        if isinstance(line, str):
            try:
                line = json.loads(line)
            except (ValueError, TypeError):
                continue
        attr = (line or {}).get("attr") or (line if isinstance(line, dict) else {})
        meta = {k: attr[k] for k in _SAFE_KEYS if k in attr}
        if meta:
            out.append(meta)
    return out
