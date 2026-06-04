"""core/atlas_api.py — lector de la Atlas Admin API (REST de gestión).

Lee CPU del cluster y slow queries del M10 SIN agregar carga de queries a la DB:
cloud.mongodb.com es una API REST de gestión, separada del cluster — no le manda
queries al M10. Base para el watchdog de DB (alertas) y un informe de salud.

Auth: HTTP Digest con las MISMAS env vars que deploy/atlas_cluster.sh:
  ATLAS_PUBLIC_KEY, ATLAS_PRIVATE_KEY, ATLAS_PROJECT_ID, ATLAS_CLUSTER_NAME

core/ no importa nada del proyecto (regla de capas) → solo os + requests.
"""
from __future__ import annotations

import os
from typing import Any

import requests
from requests.auth import HTTPDigestAuth

_BASE = "https://cloud.mongodb.com/api/atlas/v2"
# La API v2 exige versionar por Accept header. Fecha estable de la versión.
_ACCEPT = "application/vnd.atlas.2023-11-15+json"
_TIMEOUT = 30


def _cfg() -> tuple[str, str, str]:
    pub = (os.getenv("ATLAS_PUBLIC_KEY") or "").strip()
    priv = (os.getenv("ATLAS_PRIVATE_KEY") or "").strip()
    proj = (os.getenv("ATLAS_PROJECT_ID") or "").strip()
    if not (pub and priv and proj):
        raise RuntimeError(
            "Faltan env vars de Atlas: ATLAS_PUBLIC_KEY / ATLAS_PRIVATE_KEY / "
            "ATLAS_PROJECT_ID (las mismas que usa deploy/atlas_cluster.sh)."
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


def slow_queries(process_id: str, since_ms: int | None = None,
                 n_logs: int = 200) -> list[dict]:
    """Slow query logs (Performance Advisor) de un nodo. Cada item suele traer una
    `line` (log JSON de Mongo con docsExamined/planSummary/etc.). Defensivo: devuelve
    lo que venga en `slowQueries`."""
    params: dict[str, Any] = {"nLogs": n_logs}
    if since_ms is not None:
        params["since"] = since_ms
    data = get(f"/processes/{process_id}/performanceAdvisor/slowQueryLogs", params)
    return data.get("slowQueries") or []
