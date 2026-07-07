"""Diag READ-ONLY: vuelca los campos crudos de mercado.curvas para los bonos
dolar_linked (y cualquiera que se le pase por CLI) para decidir POR QUÉ no
calculan TEA.

Pregunta que responde: ¿son bullet (tienen `flujo_vencimiento` pero `flujos`
vacío) → el fix es de código (agregar fallback bullet a la rama dolar_linked,
que hoy solo mira `flujos`); o directamente no tienen flujos cargados → el fix
es de dato (cargar los flujos en el alta del bono)?

Uso:  python -m scripts.diag_dl_flujos            # todos los dolar_linked
      python -m scripts.diag_dl_flujos D31G6 TZVD8 # tickers puntuales
No escribe nada.
"""
from __future__ import annotations

import sys


def _dump(inst: dict) -> None:
    tk = inst.get("ticker_corto") or inst.get("ticker") or "?"
    curva = inst.get("curva", "?")
    flujos = inst.get("flujos") or []
    fv = inst.get("flujo_vencimiento")
    print(f"\n── {tk}  [{curva}] ──────────────────────────────")
    print(f"  fecha_vencimiento : {inst.get('fecha_vencimiento')}")
    print(f"  valor_nominal     : {inst.get('valor_nominal')}")
    print(f"  cupon_anual       : {inst.get('cupon_anual')}")
    print(f"  tasa_referencia   : {inst.get('tasa_referencia')}")
    print(f"  flujo_vencimiento : {fv}   {'← BULLET (dato bullet cargado)' if fv else '(vacío)'}")
    print(f"  n flujos          : {len(flujos)}")
    for f in flujos[:6]:
        print(f"      {f}")
    if len(flujos) > 6:
        print(f"      … (+{len(flujos) - 6} más)")

    # Veredicto por bono.
    if not flujos and fv:
        print("  → CAUSA: BULLET sin `flujos` → la rama dolar_linked no lo soporta hoy. "
              "FIX DE CÓDIGO (fallback a flujo_vencimiento en USD).")
    elif not flujos and not fv:
        print("  → CAUSA: sin flujos NI flujo_vencimiento → FIX DE DATO (cargar el flujo).")
    else:
        print("  → tiene flujos: revisar si TODOS quedan <= settlement (bono ya amortizado) "
              "o si monto_flujo_soberano da 0 (falta amortizacion_pct/cupon).")


def main() -> None:
    from core import curvas_sql

    args = [a.strip().upper() for a in sys.argv[1:] if a.strip()]
    docs = curvas_sql.cargar_todos()

    if args:
        idx = {(d.get("ticker_corto") or "").upper(): d for d in docs}
        idx.update({(d.get("ticker") or "").upper(): d for d in docs})
        seleccion = [idx[a] for a in args if a in idx]
        faltan = [a for a in args if a not in idx]
        if faltan:
            print(f"⚠️  no encontrados en mercado.curvas: {faltan}")
    else:
        seleccion = [d for d in docs if (d.get("curva") or "") == "dolar_linked"]

    print(f"Analizando {len(seleccion)} instrumentos.")
    for inst in sorted(seleccion, key=lambda d: d.get("ticker_corto") or ""):
        _dump(inst)


if __name__ == "__main__":
    main()
