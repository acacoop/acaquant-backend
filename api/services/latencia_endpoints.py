"""api/services/latencia_endpoints.py — lectura de la telemetría de latencia.

Servicio PURO (sin FastAPI) detrás de `GET /api/manager/latencia`. Lee el
agregado endpoint × hora que flushea `api/telemetria.py` y lo sirve en dos
cortes: ranking por endpoint (¿qué está lento?) y serie horaria (¿desde
cuándo / tendencia?). Con esto, "¿qué vista está lenta hoy?" es una consulta
siempre actualizada — no un diag que se corre a mano y se vence.
"""
from __future__ import annotations

from api.services._sql import _q


def get_latencia(horas: int = 24, top: int = 50) -> dict:
    """Ranking de endpoints + serie horaria de las últimas `horas`.

    Returns:
        {ventana_horas, endpoints: [{endpoint, n, avg_ms, max_ms, lentas,
         errores, pct_lentas}...], serie: [{hora, n, avg_ms, max_ms}...],
         total_requests}
    """
    horas = max(1, min(int(horas), 24 * 30))
    filas = _q(
        "SELECT endpoint, SUM(n) AS n, SUM(total_ms) AS total_ms, "
        "MAX(max_ms) AS max_ms, SUM(lentas) AS lentas, SUM(errores) AS errores "
        "FROM manager.latencia_endpoints "
        "WHERE hora >= now() - make_interval(hours => %(h)s) "
        "GROUP BY endpoint ORDER BY SUM(total_ms) DESC LIMIT %(top)s",
        {"h": horas, "top": int(top)},
    )
    endpoints = []
    for r in filas:
        n = int(r["n"] or 0)
        total = int(r["total_ms"] or 0)
        lentas = int(r["lentas"] or 0)
        endpoints.append({
            "endpoint":   r["endpoint"],
            "n":          n,
            "avg_ms":     round(total / n) if n else 0,
            "max_ms":     int(r["max_ms"] or 0),
            "lentas":     lentas,
            "errores":    int(r["errores"] or 0),
            "pct_lentas": round(lentas / n * 100, 1) if n else 0.0,
        })

    serie_rows = _q(
        "SELECT hora, SUM(n) AS n, SUM(total_ms) AS total_ms, MAX(max_ms) AS max_ms "
        "FROM manager.latencia_endpoints "
        "WHERE hora >= now() - make_interval(hours => %(h)s) "
        "GROUP BY hora ORDER BY hora",
        {"h": horas},
    )
    serie = []
    for r in serie_rows:
        n = int(r["n"] or 0)
        serie.append({
            "hora":   r["hora"].isoformat(),
            "n":      n,
            "avg_ms": round(int(r["total_ms"] or 0) / n) if n else 0,
            "max_ms": int(r["max_ms"] or 0),
        })

    return {
        "ventana_horas":  horas,
        "endpoints":      endpoints,
        "serie":          serie,
        "total_requests": sum(e["n"] for e in endpoints),
    }
