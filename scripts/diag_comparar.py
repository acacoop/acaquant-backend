"""Diagnóstico del service comparar_inversion: dump del bono crudo +
flujos que devuelve _flujos_de, para detectar diferencias.

Uso:
    python -m scripts.diag_comparar AO28D
    python -m scripts.diag_comparar AO28D TX26   # compara dos bonos
"""
from __future__ import annotations

import sys
from datetime import date

from api.db import get_db_trading
from api.services.comparar_inversion import (
    _find_bono,
    _flujos_de,
    listar_bonos_seleccionables,
)


def diag_uno(ticker_corto: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  BONO: {ticker_corto}")
    print(f"{'=' * 60}")

    # 1. Doc crudo de Trading.Curvas
    db = get_db_trading()
    doc = db["Curvas"].find_one(
        {"ticker_corto": ticker_corto},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1, "curva": 1,
         "fecha_emision": 1, "fecha_vencimiento": 1, "valor_nominal": 1,
         "cer_emision": 1, "cupon_anual": 1, "flujos": 1},
    )
    if not doc:
        print("NO ENCONTRADO en Trading.Curvas")
        return

    print(f"ticker:          {doc.get('ticker')}")
    print(f"curva:           {doc.get('curva')}")
    print(f"tipo:            {doc.get('tipo')}")
    print(f"fecha_emision:   {doc.get('fecha_emision')}")
    print(f"fecha_vencim.:   {doc.get('fecha_vencimiento')}")
    print(f"valor_nominal:   {doc.get('valor_nominal')}")
    print(f"cer_emision:     {doc.get('cer_emision')}")
    print(f"cupon_anual:     {doc.get('cupon_anual')}")

    flujos_doc = doc.get("flujos") or []
    print(f"\nflujos crudos en doc: {len(flujos_doc)}")
    for i, f in enumerate(flujos_doc):
        print(f"  [{i:2d}] {f}")

    # 2. Bono via _find_bono (lo que el service ve para construir métricas)
    found = _find_bono(ticker_corto)
    if not found:
        print("\n_find_bono(): NO ENCONTRADO en ninguna curva soportada")
    else:
        print(f"\n_find_bono(): OK — curva detectada = {found.get('_curva')}")
        print(f"  ultimo_precio = {found.get('ultimo_precio')}")
        print(f"  tea           = {found.get('tea')}")
        print(f"  duration      = {found.get('duration')}")

    # 3. Flujos via _flujos_de (lo que el endpoint manda al frontend)
    if found:
        flujos_svc, cer_proy = _flujos_de(found["_curva"], ticker_corto)
        print(f"\n_flujos_de(): {len(flujos_svc)} flujos · cer_proyectado={cer_proy}")
        for f in flujos_svc:
            print(f"  {f['fecha']}  {f['monto']:>12.4f}")
        if len(flujos_svc) != len(flujos_doc):
            print(f"\n⚠ DIFERENCIA: doc tiene {len(flujos_doc)} flujos, "
                  f"service devuelve {len(flujos_svc)}")

        # Veredicto del fix: NINGÚN flujo puede tener fecha pasada (la
        # inversión se hace hoy → solo cuentan los cupones futuros).
        hoy = date.today().isoformat()
        pasados = [f["fecha"] for f in flujos_svc if f["fecha"][:10] < hoy]
        if pasados:
            print(f"\n❌ FAIL: {len(pasados)} flujo(s) con fecha < hoy ({hoy}): "
                  f"{pasados[:5]}{'…' if len(pasados) > 5 else ''}")
        else:
            primera = flujos_svc[0]["fecha"][:10] if flujos_svc else "—"
            print(f"\n✓ OK: todos los flujos son ≥ hoy ({hoy}). Primer flujo: {primera}")


def diag_universo() -> None:
    bonos = listar_bonos_seleccionables()
    by_curva: dict[str, int] = {}
    for b in bonos:
        c = b.get("curva", "?")
        by_curva[c] = by_curva.get(c, 0) + 1
    print(f"\n{'=' * 60}")
    print(f"  UNIVERSO listar_bonos_seleccionables: {len(bonos)} bonos")
    print(f"{'=' * 60}")
    for c, n in sorted(by_curva.items()):
        print(f"  {c:15s} {n}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        diag_universo()
        return
    for tk in args:
        diag_uno(tk.upper())


if __name__ == "__main__":
    main()
