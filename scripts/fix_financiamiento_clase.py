"""fix_financiamiento_clase.py — CLASE_ACTIVO = HD o DL para toda la cartera FINANCIAMIENTO.

La regla, completa: si `cartera = FINANCIAMIENTO`, entonces `clase_activo` tiene
que ser **HD** (nominal ≤ 5.000.000) o **DL** (nominal > 5.000.000). Punto.

El nominal es la suma de la última tenencia de esa unidad.

PISA LO QUE HAYA. Es lo que lo diferencia de `jobs/assets_autofill.py`, que solo
completa campos vacíos y por eso no podía arreglar nada acá: el campo NO estaba
vacío, tenía 'FINANCIAMIENTO' (la cartera copiada dentro del campo de la clase).

Uso:
    python -m scripts.fix_financiamiento_clase              # muestra qué haría
    python -m scripts.fix_financiamiento_clase --aplicar    # escribe
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from core.postgres import get_pool
from jobs.assets_autofill import _UMBRAL_HD

_ACTOR = "script:fix_financiamiento_clase"
_BATCH = 500


def _pendientes() -> list[tuple[str, str, str, float]]:
    """(unidad, clase_actual, clase_nueva, nominal) de lo que hay que cambiar."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT a.unidad, a.clase_activo, sum(t.cantidad) "
            "FROM portafolio.assets a "
            "JOIN portafolio.tenencia t ON t.unidad = a.unidad "
            "WHERE upper(btrim(a.cartera)) = 'FINANCIAMIENTO' "
            "  AND t.fecha = (SELECT max(fecha) FROM portafolio.tenencia) "
            "  AND t.cantidad IS NOT NULL "
            "GROUP BY a.unidad, a.clase_activo "
            "ORDER BY 3")
        rows = cur.fetchall()

    out = []
    for unidad, clase, nominal in rows:
        n = float(nominal)
        nueva = "HD" if n <= _UMBRAL_HD else "DL"
        actual = (clase or "").strip().upper()
        if actual != nueva:
            out.append((unidad, actual or "(vacío)", nueva, n))
    return out


def _aplicar(pendientes: list[tuple[str, str, str, float]]) -> int:
    ts = datetime.now(UTC)
    n = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for i in range(0, len(pendientes), _BATCH):
            lote = [{"clase": nueva, "unidad": u, "actor": _ACTOR, "ts": ts}
                    for u, _act, nueva, _nom in pendientes[i:i + _BATCH]]
            cur.executemany(
                "UPDATE portafolio.assets SET clase_activo = %(clase)s, "
                "actualizado_por = %(actor)s, actualizado_at = %(ts)s "
                "WHERE unidad = %(unidad)s", lote)
            n += len(lote)
        conn.commit()
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="CLASE_ACTIVO HD/DL de la cartera FINANCIAMIENTO.")
    ap.add_argument("--aplicar", action="store_true", help="escribe (sin esto solo muestra)")
    args = ap.parse_args()

    print(f"HD si nominal ≤ {_UMBRAL_HD:,.0f} · DL si es mayor\n")
    pendientes = _pendientes()
    if not pendientes:
        print("Todo el financiamiento ya está clasificado. Nada que hacer.")
        return

    resumen: dict[str, int] = {}
    for _u, actual, nueva, _n in pendientes:
        resumen[f"{actual} → {nueva}"] = resumen.get(f"{actual} → {nueva}", 0) + 1
    print(f"A CAMBIAR: {len(pendientes)} asset(s)")
    for k, v in sorted(resumen.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print()
    for u, actual, nueva, n in pendientes[:20]:
        print(f"  {n:>18,.2f}  {actual:>14} → {nueva}   {u[:52]}")
    if len(pendientes) > 20:
        print(f"  … +{len(pendientes) - 20} más")

    if not args.aplicar:
        print("\nNo se escribió nada. Para aplicar: "
              "python -m scripts.fix_financiamiento_clase --aplicar")
        return
    print(f"\n✅ {_aplicar(pendientes)} asset(s) clasificados "
          f"(la API los relee en ≤5 min).")


if __name__ == "__main__":
    main()
