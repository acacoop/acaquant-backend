"""scripts/diag_valuacion_carteras.py — READ-ONLY. Cómo se está valuando HOY cada
CARTERA en portafolio.tenencia, y dónde NO coincide con la regla por cartera.

Causa del bug MONEDAS: el writer diario (jobs/aum.py::_calcular_valuacion) decide el
÷100 por `tipoTitulo`, no por cartera. Este diag muestra, por cartera, el divisor que
REALMENTE quedó aplicado (precio×cantidad / valuacion) vs el que dicta tu regla.

    python -m scripts.diag_valuacion_carteras            # última fecha
    python -m scripts.diag_valuacion_carteras 2025-06-30 # una fecha puntual

NO escribe nada. Sirve para: (1) ver MONEDAS ÷100, (2) listar TODAS las carteras y
decidir el divisor de las que falten (FINANCIAMIENTO, etc.) antes de tocar el writer.
"""
from __future__ import annotations

import sys
from collections import defaultdict

from core.postgres import get_pool

# Regla por cartera (la que vos definiste). 'None' = sin regla aún (a definir).
REGLA = {
    "FCI": 1, "RENTA VARIABLE": 1, "MONEDAS": 1, "DERIVADOS": 1,
    "HD": 100, "DL": 100, "ARS": 100,
}


def _div_implicito(precio, cantidad, valuacion):
    try:
        p, c, v = float(precio), float(cantidad), float(valuacion)
    except (TypeError, ValueError):
        return None
    if not v or not p or not c:
        return None
    ratio = (p * c) / v
    for d in (1, 100):
        if abs(ratio - d) / d < 0.01:
            return d
    return round(ratio, 2)  # divisor "raro" (ni 1 ni 100)


def main() -> None:
    fecha = sys.argv[1] if len(sys.argv) > 1 else None
    with get_pool().connection() as conn, conn.cursor() as cur:
        if not fecha:
            cur.execute("SELECT max(fecha) FROM portafolio.tenencia")
            fecha = cur.fetchone()[0]
        cur.execute(
            "SELECT COALESCE(NULLIF(t.cartera,''), a.cartera) AS cartera, "
            "       t.cantidad, t.precio, t.valuacion "
            "FROM portafolio.tenencia t LEFT JOIN assets a ON a.unidad = t.unidad "
            "WHERE t.fecha = %s AND t.aum = 'si'", (fecha,))
        filas = cur.fetchall()

    # por cartera: conteo por divisor implícito + total valuación
    por_cart: dict[str, dict] = defaultdict(lambda: {"n": 0, "divs": defaultdict(int),
                                                     "total": 0.0})
    for cartera, cant, prec, val in filas:
        key = (cartera or "(sin cartera)").strip().upper()
        d = _div_implicito(prec, cant, val)
        g = por_cart[key]
        g["n"] += 1
        g["divs"][d] += 1
        g["total"] += float(val or 0)

    print(f"\n=== Valuación por CARTERA · fecha {fecha} · {len(filas)} filas (aum='si') ===\n")
    print(f"{'CARTERA':<22}{'FILAS':>7}  {'DIV. APLICADO (conteo)':<28}{'REGLA':>7}  {'¿OK?':>5}  TOTAL")
    for cart in sorted(por_cart):
        g = por_cart[cart]
        divs = ", ".join(f"÷{d}×{n}" for d, n in sorted(g["divs"].items(),
                                                        key=lambda x: -x[1]))
        regla = REGLA.get(cart)
        # ¿el divisor dominante coincide con la regla?
        dom = max(g["divs"].items(), key=lambda x: x[1])[0] if g["divs"] else None
        if regla is None:
            ok = "??"      # cartera sin regla definida
        elif dom == regla:
            ok = "ok"
        else:
            ok = "MAL"
        regla_s = "—" if regla is None else f"÷{regla}"
        print(f"{cart:<22}{g['n']:>7}  {divs:<28}{regla_s:>7}  {ok:>5}  {g['total']:>16,.0f}")

    sin_regla = sorted(c for c in por_cart if REGLA.get(c) is None)
    print("\nCarteras SIN regla definida (decidí el divisor de estas):")
    print("   ", sin_regla or "ninguna")
    print("\nLeyenda: 'DIV. APLICADO' = precio×cantidad/valuacion que quedó guardado.")
    print("'MAL' = el divisor guardado NO coincide con tu regla por cartera (ej. MONEDAS ÷100).")


if __name__ == "__main__":
    main()
