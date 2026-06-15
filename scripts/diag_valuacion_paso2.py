"""scripts/diag_valuacion_paso2.py — READ-ONLY. Verifica cómo recalcular la
`valuacion` en portafolio.tenencia tras una importación de PRECIOS (paso 2).

El problema: tenencia NO guarda el tipo de instrumento, y el ÷100 de la
valuación depende de él (bonos/letras/ONs cotizan en paridad). Hay dos formas
de recuperar el divisor por `unidad`; este diag mide cuál es confiable ANTES de
escribir el writer (REGLA #2 — no asumir):

  A) CATÁLOGO  — join a `assets` (instrumento/clase_activo) + TIPOS_DIVISOR_100.
  B) EMPÍRICO  — en una fecha NO tocada, precio*cantidad/valuacion ≈ 1 ó 100
                 → recupera el divisor REAL sin depender de ningún catálogo.

Uso:  python -m scripts.diag_valuacion_paso2 2025-06-30
(la fecha es la que importaste; sin arg lista las fechas disponibles)
"""
from __future__ import annotations

import sys

from core.postgres import get_pool
from jobs.aum import TIPOS_DIVISOR_100, TIPOS_FUTUROS, _calcular_valuacion


def _divisor_empirico(precio, cantidad, valuacion) -> int | None:
    """precio*cantidad/valuacion redondeado a 1 ó 100; None si no es limpio."""
    try:
        p, c, v = float(precio), float(cantidad), float(valuacion)
    except (TypeError, ValueError):
        return None
    if not v or not p or not c:
        return None
    ratio = (p * c) / v
    for d in (1, 100):
        if abs(ratio - d) / d < 0.01:   # ±1%
            return d
    return None


def _tipo_de(instrumento, clase_activo) -> str:
    return (instrumento or clase_activo or "").strip()


def main() -> None:
    if len(sys.argv) < 2:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT fecha, count(*) FROM portafolio.tenencia "
                        "GROUP BY fecha ORDER BY fecha DESC LIMIT 30")
            print("Pasá una fecha. Disponibles (fecha · filas):")
            for f, n in cur.fetchall():
                print(f"  {f}  ·  {n}")
        return

    fecha = sys.argv[1]
    with get_pool().connection() as conn, conn.cursor() as cur:
        # Filas de la fecha importada + tipo del catálogo.
        cur.execute(
            "SELECT t.unidad, t.cantidad, t.precio, t.valuacion, "
            "       a.instrumento, a.clase_activo "
            "FROM portafolio.tenencia t "
            "LEFT JOIN assets a ON a.unidad = t.unidad "
            "WHERE t.fecha = %s", (fecha,))
        filas = cur.fetchall()
        if not filas:
            print(f"Sin filas para {fecha}.")
            return
        unidades = sorted({r[0] for r in filas})

        # Divisor EMPÍRICO por unidad: 1 fila de referencia de otra fecha (la más
        # reciente anterior con valuación consistente). DISTINCT ON usa el índice.
        cur.execute(
            "SELECT DISTINCT ON (unidad) unidad, precio, cantidad, valuacion "
            "FROM portafolio.tenencia "
            "WHERE fecha < %s AND unidad = ANY(%s) AND valuacion <> 0 AND precio <> 0 "
            "ORDER BY unidad, fecha DESC", (fecha, unidades))
        emp: dict[str, int | None] = {}
        for u, p, c, v in cur.fetchall():
            emp[u] = _divisor_empirico(p, c, v)

    # Clasificación + comparación.
    sin_assets = 0
    sin_ref_emp = 0
    mismatches: list[tuple] = []
    tipos_vistos: dict[str, int] = {}
    total_antes = total_despues = 0.0
    no_clasificable: list[str] = []

    for unidad, cantidad, precio, val_old, instrumento, clase in filas:
        tipo = _tipo_de(instrumento, clase)
        if instrumento is None and clase is None:
            sin_assets += 1
        # Divisor por catálogo (lo que decide _calcular_valuacion).
        if any(f.lower() in tipo.lower() for f in TIPOS_FUTUROS):
            div_cat = "futuro"
        elif tipo in TIPOS_DIVISOR_100:
            div_cat = 100
        elif tipo:
            div_cat = 1
        else:
            div_cat = None
            no_clasificable.append(unidad)
        tipos_vistos[tipo or "(vacío)"] = tipos_vistos.get(tipo or "(vacío)", 0) + 1

        div_emp = emp.get(unidad)
        if div_emp is None:
            sin_ref_emp += 1
        # Mismatch sólo cuando ambos son numéricos y difieren.
        if isinstance(div_cat, int) and div_emp is not None and div_cat != div_emp:
            mismatches.append((unidad, tipo, div_cat, div_emp))

        # Valuación nueva = la del motor (catálogo) usando el precio ya actualizado.
        val_new = _calcular_valuacion(
            {"precio": float(precio or 0), "cantidad": float(cantidad or 0), "tipoTitulo": tipo})
        total_antes += float(val_old or 0)
        total_despues += val_new

    print(f"\n=== DIAG paso 2 · fecha {fecha} · {len(filas)} filas · {len(unidades)} unidades ===\n")
    print("Tipos detectados (instrumento|clase_activo del catálogo) → conteo:")
    for t, n in sorted(tipos_vistos.items(), key=lambda x: -x[1]):
        div = ("futuro" if any(f.lower() in t.lower() for f in TIPOS_FUTUROS)
               else 100 if t in TIPOS_DIVISOR_100 else 1 if t != "(vacío)" else "??")
        print(f"  [{n:>4}] divisor={div!s:>6}  {t}")

    print(f"\nUnidades SIN fila en assets (catálogo): {sin_assets}")
    print(f"Unidades NO clasificables por catálogo (tipo vacío): {len(set(no_clasificable))}")
    if no_clasificable:
        print("   ", sorted(set(no_clasificable))[:20])
    print(f"Unidades SIN referencia empírica (sólo existen en {fecha}): {sin_ref_emp}")

    print(f"\nMISMATCHES catálogo↔empírico: {len(mismatches)}")
    for u, t, dc, de in mismatches[:30]:
        print(f"   {u:<22} cat={dc!s:>6} emp={de:>4}  tipo='{t}'")

    print(f"\nTOTAL valuación  ANTES (guardada):  {total_antes:,.2f}")
    print(f"TOTAL valuación  DESPUÉS (catálogo): {total_despues:,.2f}")
    print(f"Δ = {total_despues - total_antes:,.2f}")
    print("\n→ Si MISMATCHES=0 y 'no clasificables'=0, el método CATÁLOGO es seguro.")
    print("→ Si hay mismatches, el empírico es la verdad: hay que recuperar el divisor de otra fecha.")


if __name__ == "__main__":
    main()
