"""Diagnóstico read-only: pega a Aunesa una fecha y muestra las líneas raw
de un comprobante específico — antes y después del parser nuestro.

Útil para auditar si el `importe` o `moneda` consolidados que terminan en
`CashFlow.NegocioMovimientos` son fieles al dato de Aunesa o si nuestro
parser introduce error.

NO escribe nada — solo imprime.

Uso:
    python -m scripts.inspect_boleto_aunesa 2025-07-28 "BOL 2025112077"
"""
from __future__ import annotations

import sys
from datetime import datetime

from api.services import aunesa_negocio as svc


def main() -> None:
    if len(sys.argv) < 3:
        print("Uso: python -m scripts.inspect_boleto_aunesa YYYY-MM-DD <COMPROBANTE>")
        print('Ej : python -m scripts.inspect_boleto_aunesa 2025-07-28 "BOL 2025112077"')
        sys.exit(1)

    fecha_str = sys.argv[1]
    comprobante = sys.argv[2]
    try:
        fecha_d = datetime.strptime(fecha_str, "%Y-%m-%d").date()
    except ValueError:
        print(f"Formato inválido: {fecha_str}. Usar YYYY-MM-DD.")
        sys.exit(1)

    print(f"Pidiendo a Aunesa fecha={fecha_str}…")
    res = svc.fetch_y_consolidar(fecha=fecha_d)
    movimientos = res.get("movimientos", [])
    boletos = res.get("boletos", [])
    print(f"Aunesa devolvió: {len(movimientos)} líneas raw → {len(boletos)} boletos consolidados\n")

    # Filtrar líneas raw por comprobante
    lineas_raw = [m for m in movimientos if m.get("comprobante") == comprobante]
    if not lineas_raw:
        print(f"❌ Sin líneas raw para comprobante {comprobante!r} en {fecha_str}.")
        # Ayuda: listar los comprobantes disponibles
        comps = sorted({m.get("comprobante") for m in movimientos if m.get("comprobante")})
        print(f"\nComprobantes disponibles ({len(comps)}):")
        for c in comps[:50]:
            print(f"  {c}")
        if len(comps) > 50:
            print(f"  ... y {len(comps) - 50} más")
        sys.exit(2)

    print("═══════════════════════════════════════════════════════════════")
    print(f"COMPROBANTE: {comprobante}")
    print(f"Líneas raw: {len(lineas_raw)}")
    print("═══════════════════════════════════════════════════════════════\n")

    for i, m in enumerate(lineas_raw, 1):
        print(f"─── LÍNEA {i} ──────────────────────────────────────────────")
        # Imprimir todos los campos de la línea (raw + enriquecidos por _enriquecer).
        for k, v in sorted(m.items()):
            print(f"  {k}: {v!r}")
        print()

    # Buscar el boleto consolidado correspondiente
    boletos_match = [b for b in boletos if b.get("comprobante") == comprobante]
    if boletos_match:
        b = boletos_match[0]
        print("═══════════════════════════════════════════════════════════════")
        print("BOLETO CONSOLIDADO (lo que termina en NegocioMovimientos):")
        print("═══════════════════════════════════════════════════════════════")
        for k in ["categoria", "op", "ticker", "cantidad", "precio",
                  "importe", "moneda", "plazo", "informacion", "n_lineas"]:
            print(f"  {k}: {b.get(k)!r}")


if __name__ == "__main__":
    main()
