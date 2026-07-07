"""Fix de DATO scopeado (REGLA #4): normaliza `amortizacion_pct` de los bonos
dolar_linked que quedaron cargados en convención FRACCIÓN (1.0 = 100%) en vez de
PORCENTAJE (100 = 100%), que es lo que espera el motor (`monto_flujo_soberano`
hace `amortizacion_pct / 100 · VN`).

Diagnóstico (scripts.diag_dl_engine, 2026-07-07): D31G6 y TZVD8 tenían
`amortizacion_pct: 1.0` → monto=1 USD (100× chico) → XIRR explota → TEA "--".
Los otros 5 dolar_linked usan `100` y calculan bien.

Alcance: SOLO los tickers de TICKERS (2 docs). Idempotente: un flujo que ya está
en porcentaje (>2) no se toca. NO recalcula tasas — eso lo hace jobs.backfill_tasas
(o el motor al reciclar el cache).

Uso (en el Droplet):  python -m scripts.fix_amort_pct_dolar_linked
                      python -m scripts.fix_amort_pct_dolar_linked --dry-run
"""
from __future__ import annotations

import sys

TICKERS = ["D31G6", "TZVD8"]


def main() -> None:
    dry = "--dry-run" in sys.argv

    from api.services.ons import _upsert_curva_doc
    from core import curvas_sql

    for tc in TICKERS:
        inst = curvas_sql.find_one(tc)
        if not inst:
            print(f"⚠️  {tc}: no está en mercado.curvas — salteo.")
            continue

        flujos = inst.get("flujos") or []
        cambios = []
        nuevos = []
        for f in flujos:
            f2 = dict(f)
            ap = f.get("amortizacion_pct")
            # Fracción → porcentaje: 0 < ap <= 2 significa que 1.0 era "100%".
            if ap is not None and 0 < float(ap) <= 2:
                f2["amortizacion_pct"] = round(float(ap) * 100, 6)
                cambios.append((f.get("fecha"), ap, f2["amortizacion_pct"]))
            nuevos.append(f2)

        if not cambios:
            print(f"✅ {tc}: ya está en porcentaje ({[f.get('amortizacion_pct') for f in flujos]}) — nada que hacer.")
            continue

        print(f"🔧 {tc}: {len(cambios)} flujo(s) a corregir:")
        for fecha, viejo, nuevo in cambios:
            print(f"     {fecha}: amortizacion_pct {viejo} → {nuevo}")

        if dry:
            print("     (dry-run: no se escribió)")
            continue

        _upsert_curva_doc({"ticker_corto": tc, "flujos": nuevos})
        print(f"     ✔ guardado en mercado.curvas ({tc}).")

    if not dry:
        print("\nListo. Para que la TEA aparezca YA: correr `python -m jobs.backfill_tasas` "
              "(o reiniciar motor_curvas). El motor solo por sí mismo recalcula cuando "
              "cambia el precio o recicla CER/MEP/A3500.")


if __name__ == "__main__":
    main()
