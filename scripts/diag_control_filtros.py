"""scripts/diag_control_filtros.py — ¿los filtros madre (operador/niveles) realmente
cambian el output de Control Comercial? Server-side, read-only.

Corre las funciones del backend CON y SIN un filtro real y compara. Decisivo:
  • si el output NO cambia con un filtro real → el bug está en el BACKEND (lo vemos acá).
  • si cambia → el backend filtra OK y el problema es de deploy/frontend (Vercel/caché).

No imprime montos (solo conteos + si cambió sí/no) → salida segura para pegar.

Uso: python -m scripts.diag_control_filtros
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

from api.services import control_comercial_sql as cc
from api.services.comercial_sql import _q


def _fila_total(d: dict) -> dict:
    for f in d.get("filas", []):
        if f.get("periodo") == "Total":
            return f
    return {}


def main() -> int:
    hoy = date.today()
    desde, hasta = (hoy - timedelta(days=120)).isoformat(), hoy.isoformat()

    niveles = _q("SELECT nivel_1, COUNT(*) AS n FROM comitentes WHERE estado='Activa' "
                 "AND nivel_1 IS NOT NULL AND nivel_1 <> '' GROUP BY nivel_1 ORDER BY n DESC")
    print("nivel_1 disponibles (top 8):")
    for r in niveles[:8]:
        print(f"    {r['nivel_1']!r:32} {r['n']} cuentas")
    if not niveles:
        print("✗ no hay nivel_1 en comitentes — no puedo probar el filtro de nivel")
        return 1
    pick = niveles[min(1, len(niveles) - 1)]["nivel_1"]   # 2do más común (subconjunto real)
    print(f"\n>>> Probando filtro nivel_1 = {pick!r}\n")

    # ── Tabla 1: Totales ALyC (fila 'Total') ──────────────────────────────────
    base1 = _fila_total(cc.datos_totales_alyc(moneda="ARS"))
    filt1 = _fila_total(cc.datos_totales_alyc(moneda="ARS", nivel_1=(pick,)))
    cambia1 = base1.get("clientes_activos") != filt1.get("clientes_activos") \
        or base1.get("volumen") != filt1.get("volumen")
    print("[Tabla 1] Totales ALyC — fila 'Total':")
    print(f"    sin filtro : activos={base1.get('clientes_activos')}")
    print(f"    con filtro : activos={filt1.get('clientes_activos')}")
    print(f"    → {'CAMBIA ✓ (filtro impacta)' if cambia1 else 'IGUAL ✗ (filtro NO impacta)'}")

    # ── Tabla 2: Por operador ─────────────────────────────────────────────────
    base2 = cc.datos_por_operador(desde=desde, hasta=hasta, moneda="ARS")
    filt2 = cc.datos_por_operador(desde=desde, hasta=hasta, moneda="ARS", nivel_1=(pick,))
    vol_b = round(sum(f["volumen"] for f in base2["filas"]), 2)
    vol_f = round(sum(f["volumen"] for f in filt2["filas"]), 2)
    cambia2 = len(base2["filas"]) != len(filt2["filas"]) or vol_b != vol_f
    print(f"\n[Tabla 2] Por operador [{desde} → {hasta}]:")
    print(f"    sin filtro : {len(base2['filas'])} operadores")
    print(f"    con filtro : {len(filt2['filas'])} operadores")
    print(f"    volumen total cambió: {'sí' if vol_b != vol_f else 'no'}")
    print(f"    → {'CAMBIA ✓' if cambia2 else 'IGUAL ✗ (filtro NO impacta)'}")

    # ── Tabla 2: filtro por operador (debería devolver solo ese operador) ──────
    ops = _q("SELECT DISTINCT operador_email FROM comitentes WHERE estado='Activa' "
             "AND operador_email IS NOT NULL AND operador_email <> '' LIMIT 1")
    if ops:
        email = ops[0]["operador_email"]
        f3 = cc.datos_por_operador(desde=desde, hasta=hasta, moneda="ARS", operador=(email,))
        print(f"\n[Tabla 2] filtro operador={email!r}: {len(f3['filas'])} filas "
              f"→ {'OK ✓ (≤1)' if len(f3['filas']) <= 1 else 'RARO ✗ (debería ser 1)'}")

    print("\nResumen: si arriba dice 'IGUAL ✗' → el backend NO está filtrando (bug backend).")
    print("         si dice 'CAMBIA ✓' → backend OK; el problema es deploy/frontend/caché.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
