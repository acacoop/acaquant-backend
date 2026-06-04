"""scripts/diag_comercial_cache_check.py — VERIFICA que el rollup == camino viejo.

REGLA #2: antes de flipear informe_comercial a leer el rollup, confirmar que los
números coinciden. Compara los GRAN TOTALES del camino vivo (informe_comercial,
COLLSCAN de NegocioMovimientos + Operaciones) contra los del rollup ComercialCache:

    vol_total · vol_mes · ar_total · ar_mes · n_ops

Si todos matchean (dentro de $1 / 1 op), el rollup es fiel y se puede migrar el
service. Read-only.

Prereq: correr antes el rollup → python -m jobs.comercial_rollup --full
Correr en el Droplet:
    python -m scripts.diag_comercial_cache_check
"""
from __future__ import annotations

from api.services.comercial import _hoy_art, informe_comercial
from core.mongo import get_mongo_client_read


def _totales_vivos() -> dict:
    """Σ sobre el output real de informe_comercial(ARS) — ejercita el camino viejo."""
    inf = informe_comercial(moneda="ARS")
    com = inf["comerciales"]
    return {
        "vol_total": round(sum(o["vol_total"] for o in com), 2),
        "vol_mes":   round(sum(o["vol_mes"] for o in com), 2),
        "ar_total":  round(sum(o["ar_total"] for o in com), 2),
        "ar_mes":    round(sum(o["ar_mes"] for o in com), 2),
        "n_ops":     sum(o["n_ops"] for o in com),
    }


def _totales_rollup(mes_start: str) -> dict:
    """Σ desde Clientes.ComercialCache (lo que leería el informe nuevo)."""
    col = get_mongo_client_read()["Clientes"]["ComercialCache"]
    r = next(iter(col.aggregate([
        {"$group": {
            "_id": None,
            "vol_total": {"$sum": "$vol"},
            "vol_mes": {"$sum": {"$cond": [{"$gte": ["$fecha", mes_start]}, "$vol", 0]}},
            "ar_total": {"$sum": "$arancel"},
            "ar_mes": {"$sum": {"$cond": [{"$gte": ["$fecha", mes_start]}, "$arancel", 0]}},
            "n_ops": {"$sum": "$n_ops"},
        }},
    ])), None) or {}
    return {
        "vol_total": round(float(r.get("vol_total") or 0.0), 2),
        "vol_mes":   round(float(r.get("vol_mes") or 0.0), 2),
        "ar_total":  round(float(r.get("ar_total") or 0.0), 2),
        "ar_mes":    round(float(r.get("ar_mes") or 0.0), 2),
        "n_ops":     int(r.get("n_ops") or 0),
    }


def main() -> int:
    col = get_mongo_client_read()["Clientes"]["ComercialCache"]
    n_docs = col.estimated_document_count()
    if not n_docs:
        print("❌ Clientes.ComercialCache vacío. Correr primero: python -m jobs.comercial_rollup --full")
        return 1

    mes_start = _hoy_art().replace(day=1).isoformat()
    print(f"Rollup: {n_docs:,} filas · mes_start={mes_start}\n")

    vivo = _totales_vivos()
    roll = _totales_rollup(mes_start)

    print(f"{'métrica':12} {'VIVO (informe)':>18} {'ROLLUP (cache)':>18} {'Δ':>14}  estado")
    ok = True
    for k in ("vol_total", "vol_mes", "ar_total", "ar_mes", "n_ops"):
        diff = roll[k] - vivo[k]
        tol = 1.0 if k != "n_ops" else 0
        bien = abs(diff) <= tol
        ok = ok and bien
        print(f"{k:12} {vivo[k]:>18,.2f} {roll[k]:>18,.2f} {diff:>14,.2f}  "
              f"{'✓' if bien else '❌ DESCUADRA'}")

    print("\n" + ("✅ Rollup FIEL — se puede migrar el service." if ok
                  else "❌ NO migrar: el rollup descuadra. Revisar match/grano antes de flipear."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
