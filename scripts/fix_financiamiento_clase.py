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


def _pendientes() -> tuple[list[tuple[str, str, str, float]], int, int]:
    """(a_cambiar, ya_ok, sin_nominal).

    El nominal sale de la ÚLTIMA tenencia que tuvo CADA unidad, no de la de hoy.
    Un pagaré que ya venció o que se cobró no tiene tenencia hoy: mirando solo el
    último día se caían del universo y quedaban sin clasificar para siempre
    (que es exactamente lo que pasó la primera vez).
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "WITH fin AS ("
            "    SELECT unidad, clase_activo FROM portafolio.assets"
            "     WHERE upper(btrim(cartera)) = 'FINANCIAMIENTO'"
            "), ult AS ("
            "    SELECT t.unidad, max(t.fecha) AS fecha"
            "      FROM portafolio.tenencia t JOIN fin f ON f.unidad = t.unidad"
            "     GROUP BY t.unidad"
            ") "
            "SELECT f.unidad, f.clase_activo, sum(t.cantidad) "
            "  FROM fin f "
            "  LEFT JOIN ult u ON u.unidad = f.unidad "
            "  LEFT JOIN portafolio.tenencia t "
            "         ON t.unidad = f.unidad AND t.fecha = u.fecha "
            " GROUP BY f.unidad, f.clase_activo "
            " ORDER BY 3 NULLS LAST")
        rows = cur.fetchall()

    out: list[tuple[str, str, str, float]] = []
    ya_ok = sin_nominal = 0
    for unidad, clase, nominal in rows:
        if nominal is None:
            sin_nominal += 1        # nunca tuvo tenencia: no hay de dónde inferir
            continue
        n = float(nominal)
        nueva = "HD" if n <= _UMBRAL_HD else "DL"
        actual = (clase or "").strip().upper()
        if actual == nueva:
            ya_ok += 1
        else:
            out.append((unidad, actual or "(vacío)", nueva, n))
    return out, ya_ok, sin_nominal


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
    pendientes, ya_ok, sin_nominal = _pendientes()
    print(f"cartera FINANCIAMIENTO: {len(pendientes) + ya_ok + sin_nominal} assets "
          f"· ya OK: {ya_ok} · a cambiar: {len(pendientes)} · sin tenencia nunca: {sin_nominal}")
    if sin_nominal:
        print(f"  ({sin_nominal} no tuvieron NUNCA una tenencia → no hay nominal "
              f"del cual inferir; quedan vacíos y se cargan a mano si hace falta)")
    print()
    if not pendientes:
        print("Todo lo clasificable ya está clasificado. Nada que hacer.")
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
