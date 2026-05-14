"""diag_aum_unidad.py — audita docs de Valuaciones.AuM para una unidad
puntual.

Sirve para diagnosticar bugs como el de DHSFO ON CREDICUOT (2026-05-14):
la valuación viene como P×Q en lugar de P×Q/100 porque `tipoTitulo`
devuelto por Aunesa no matchea exactamente alguno de los items de
TIPOS_DIVISOR_100 en jobs/aum.py.

Output por doc (top 10 más recientes):
  fecha_snapshot, id_cuenta, tipoTitulo, cantidad, precio, valuacion,
  valuacion_si_div100 (cómo quedaría con P×Q/100), delta.

Más resumen: cuántos docs distintos hay, qué `tipoTitulo` distintos
aparecieron en esa unidad (si hay más de uno hay inconsistencia
Aunesa-side).

Uso:
    python -m scripts.diag_aum_unidad "DHSFO"
    python -m scripts.diag_aum_unidad "ON CREDICUOT"     # regex parcial
"""
from __future__ import annotations

import re
import sys
from collections import Counter

from core.mongo import get_mongo_client


def run(filtro: str) -> None:
    print("=" * 100)
    print(f"DIAG Valuaciones.AuM — filtro: {filtro!r}")
    print("=" * 100)

    db = get_mongo_client()["Valuaciones"]
    col = db["AuM"]

    rx = re.compile(re.escape(filtro), re.IGNORECASE)
    docs = list(col.find(
        {"unidad": rx},
        {"_id": 0, "fecha_snapshot": 1, "id_cuenta": 1, "unidad": 1,
         "tipoTitulo": 1, "cantidad": 1, "precio": 1, "valuacion": 1},
    ))
    print(f"\nTotal docs encontrados: {len(docs)}")
    if not docs:
        print("∅ Nada con ese filtro en Valuaciones.AuM.")
        return

    # ── Unidades distintas que matchearon (puede ser más de una) ────
    unidades = Counter(d.get("unidad", "?") for d in docs)
    print(f"\nUnidades distintas ({len(unidades)}):")
    for u, n in unidades.most_common(10):
        print(f"   {n:5d}  {u}")

    # ── tipoTitulo distintos — ESTO ES CLAVE ────────────────────────
    tipos = Counter(d.get("tipoTitulo", "?") for d in docs)
    print(f"\ntipoTitulo distintos ({len(tipos)}):")
    for t, n in tipos.most_common(10):
        print(f"   {n:5d}  {t!r}")

    # ── Top 10 docs más recientes ───────────────────────────────────
    docs.sort(key=lambda d: d.get("fecha_snapshot", ""), reverse=True)
    print("\nÚltimos 10 docs:")
    print(f"  {'FECHA':<12} {'CTA':<6} {'CANT':>14} {'PRECIO':>12} {'VALUAC':>16} {'/100':>16} {'TIPO_TITULO':<35}")
    for d in docs[:10]:
        cant   = float(d.get("cantidad") or 0)
        precio = float(d.get("precio")   or 0)
        valuac = float(d.get("valuacion") or 0)
        si_100 = cant * precio / 100 if precio else 0
        print(f"  {str(d.get('fecha_snapshot',''))[:10]:<12} "
              f"{str(d.get('id_cuenta',''))[:6]:<6} "
              f"{cant:>14,.2f} {precio:>12,.4f} "
              f"{valuac:>16,.2f} {si_100:>16,.2f} "
              f"{str(d.get('tipoTitulo',''))[:35]:<35}")

    # ── Veredicto ───────────────────────────────────────────────────
    # Si el ratio mediana(valuacion / (cant*precio)) es ~1 → NO se está dividiendo.
    # Si es ~0.01 → SÍ se está dividiendo (P×Q/100).
    ratios = []
    for d in docs:
        cant   = float(d.get("cantidad") or 0)
        precio = float(d.get("precio")   or 0)
        valuac = float(d.get("valuacion") or 0)
        if cant and precio:
            ratios.append(valuac / (cant * precio))
    if ratios:
        ratios.sort()
        med = ratios[len(ratios) // 2]
        print(f"\nRatio mediano valuacion / (cant × precio): {med:.4f}")
        if abs(med - 1) < 0.01:
            print("  → valuacion = P×Q (NO se divide por 100). Si esta unidad es ON/bono → bug.")
        elif abs(med - 0.01) < 0.001:
            print("  → valuacion = P×Q/100. Está aplicando bien el divisor.")
        else:
            print(f"  → ratio raro. Verificar manualmente.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.diag_aum_unidad <substring_de_unidad>")
        print("Ejemplos: DHSFO  |  ON CREDICUOT  |  CREDICUOT")
        sys.exit(1)
    run(filtro=" ".join(sys.argv[1:]))
